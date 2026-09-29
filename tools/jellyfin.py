"""Jellyfin 10.11 API adapter for one configured user; credentials stay here."""
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

TICKS = 10_000_000
VIDEO_TYPES = 'Movie,Episode,Video'


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-fA-F0-9-]{32,36}', value):
        raise ApiError(400, 'Invalid Jellyfin item ID')
    return value


def brief(item):
    user = item.get('UserData') or {}
    duration = (item.get('RunTimeTicks') or 0) / TICKS
    return dict(id=item['Id'], name=str(item.get('Name') or 'Untitled')[:180],
        type=item.get('Type', 'Video'), folder=bool(item.get('IsFolder')),
        duration=duration, resume=min(duration, (user.get('PlaybackPositionTicks') or 0) / TICKS),
        played=bool(user.get('Played')), year=item.get('ProductionYear'),
        series=str(item.get('SeriesName') or '')[:120],
        episode=item.get('IndexNumber'), season=item.get('ParentIndexNumber'),
        overview=str(item.get('Overview') or '')[:800])


class SameServerRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise ApiError(502, 'Jellyfin redirected to a different server; configure its final URL')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Jellyfin:
    def __init__(self, config):
        self.base = config.get('JELLYFIN_URL', '').rstrip('/')
        url = urllib.parse.urlsplit(self.base)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.query or url.fragment:
            raise ValueError('JELLYFIN_URL must be an http(s) server URL')
        self.username = config.get('JELLYFIN_USERNAME', '')
        self.password = config.get('JELLYFIN_PASSWORD', '')
        self.token = config.get('JELLYFIN_TOKEN', '')
        self.user_id = config.get('JELLYFIN_USER_ID', '')
        self.lock = threading.RLock()
        self.opener = urllib.request.build_opener(SameServerRedirect())
        self.auth_base = 'MediaBrowser Client="Playdate Jellyfin", Device="PDS Bridge", DeviceId="playdate-pds-bridge", Version="0.2.0"'

    def _open(self, path, params=None, data=None, *, auth=True, headers=None, retry=True):
        url = self.base + path
        if params:
            url += '?' + urllib.parse.urlencode({k: str(v).lower() if isinstance(v, bool) else v
                                                 for k, v in params.items() if v is not None})
        auth_header = self.auth_base
        used_token = None
        if auth:
            self.login()
            # Jellyfin's token is never placed in a URL, FFmpeg arguments, or log.
            if not re.fullmatch(r'[a-zA-Z0-9_-]+', self.token):
                raise ApiError(503, 'Invalid configured Jellyfin token')
            used_token = self.token
            auth_header += ', Token="' + used_token + '"'
        request_headers = {'X-Emby-Authorization': auth_header, 'Accept': 'application/json',
                           'User-Agent': 'JellyfinPDS/0.2.0'}
        request_headers.update(headers or {})
        body = None if data is None else json.dumps(data).encode()
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=body, headers=request_headers)
        try:
            return self.opener.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status in (401, 403):
                if auth and retry and self.username:
                    with self.lock:
                        if self.token == used_token:
                            self.token, self.user_id = '', ''
                        self.login()
                    return self._open(path, params, data, auth=auth, headers=headers, retry=False)
                raise ApiError(502, 'Jellyfin rejected the login or this account lacks access') from None
            if status == 404:
                raise ApiError(404, 'Jellyfin item was not found') from None
            raise ApiError(502, f'Jellyfin returned HTTP {status}') from None
        except (urllib.error.URLError, OSError):
            raise ApiError(502, 'Cannot reach Jellyfin; check the server URL and network') from None

    def request(self, path, params=None, data=None, *, auth=True):
        with self._open(path, params, data, auth=auth) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ApiError(502, 'Jellyfin response exceeded the metadata limit')
            try:
                return json.loads(raw) if raw else {}
            except (ValueError, UnicodeError):
                raise ApiError(502, 'Jellyfin returned invalid JSON') from None

    def login(self):
        with self.lock:
            if self.token and self.user_id:
                return
            if self.token:
                # _open would call login recursively here; require the explicit
                # user ID for externally supplied tokens.
                raise ApiError(503, 'Set JELLYFIN_USER_ID for the configured access token')
            if not self.username:
                raise ApiError(503, 'Set JELLYFIN_USERNAME and JELLYFIN_PASSWORD in the bridge .env file')
            result = self.request('/Users/AuthenticateByName', data={
                'Username': self.username, 'Pw': self.password}, auth=False)
            self.token = result.get('AccessToken', '')
            self.user_id = (result.get('User') or {}).get('Id', '')
            if not self.token or not self.user_id:
                raise ApiError(502, 'Jellyfin did not return a user access token')

    def status(self):
        self.login()
        info = self.request('/System/Info/Public', auth=False)
        return {'server': info.get('ServerName', 'Jellyfin'), 'version': info.get('Version', ''),
                'user': self.username or 'Configured user'}

    def libraries(self):
        self.login()
        result = self.request('/UserViews', {'UserId': self.user_id})
        items = [brief(item) for item in result.get('Items', [])
                 if item.get('CollectionType') not in ('music', 'books', 'photos', 'livetv')]
        return {'items': items, 'total': len(items), 'start': 0}

    def items(self, parent=None, start=0, search='', resume=False):
        self.login()
        params = dict(UserId=self.user_id, StartIndex=start, Limit=20,
            Fields='Overview', EnableImages=False, EnableUserData=True,
            Recursive=bool(search or resume), SortBy='SortName', SortOrder='Ascending')
        path = '/Items'
        if parent:
            params['ParentId'] = identifier(parent)
            params['SortBy'] = 'ParentIndexNumber,IndexNumber,SortName'
        params['IncludeItemTypes'] = VIDEO_TYPES if search or resume else VIDEO_TYPES+',Series,Season,BoxSet,Folder,CollectionFolder'
        if search:
            params['SearchTerm'] = search[:120]
        if resume:
            path = '/Items/Resume'
            params.update(MediaTypes='Video', SortBy='DatePlayed', SortOrder='Descending')
        result = self.request(path, params)
        return {'items': [brief(item) for item in result.get('Items', [])],
                'total': result.get('TotalRecordCount', 0), 'start': start}

    def item(self, item_id):
        self.login()
        result = self.request('/Items', {'UserId': self.user_id, 'Ids': identifier(item_id),
            'Fields': 'Overview,MediaSources,MediaStreams', 'EnableImages': False, 'EnableUserData': True})
        if not result.get('Items'):
            raise ApiError(404, 'This video is no longer available')
        return result['Items'][0]

    def playback(self, item_id):
        item = self.item(item_id)
        if item.get('IsFolder') or item.get('Type') not in VIDEO_TYPES.split(','):
            raise ApiError(400, 'Choose a movie, episode, or video')
        info = self.request('/Items/'+identifier(item_id)+'/PlaybackInfo',
            data={'UserId': self.user_id, 'IsPlayback': False, 'AutoOpenLiveStream': False})
        sources = info.get('MediaSources') or []
        source = next((s for s in sources if not s.get('RequiresOpening') and not s.get('IsInfiniteStream')), None)
        if not source:
            raise ApiError(422, 'No finite video source is available; live TV is not supported')
        streams = source.get('MediaStreams') or []
        if not any(s.get('Type') == 'Video' for s in streams):
            raise ApiError(422, 'The selected source has no video track')
        duration = (source.get('RunTimeTicks') or item.get('RunTimeTicks') or 0) / TICKS
        if duration <= 0:
            raise ApiError(422, 'Jellyfin has not determined the video duration')
        audio = [s for s in streams if s.get('Type') == 'Audio' and not s.get('IsExternal')]
        chosen = next((s for s in audio if s.get('Index') == source.get('DefaultAudioStreamIndex')), audio[0] if audio else None)
        return {'item': brief(item), 'source_id': str(source['Id']), 'duration': duration,
            'play_session_id': info.get('PlaySessionId'),
            'audio_index': chosen['Index'] if chosen else None, 'has_audio': chosen is not None}

    def source(self, item_id, source_id, range_header=None):
        headers = {'Accept': '*/*'}
        if range_header:
            if not re.fullmatch(r'bytes=\d*-\d*', range_header):
                raise ApiError(416, 'Only a single byte range is supported')
            headers['Range'] = range_header
        return self._open('/Videos/'+identifier(item_id)+'/stream',
            {'Static': True, 'MediaSourceId': source_id, 'UserId': self.user_id}, headers=headers)

    def report(self, job, kind, position, paused=False):
        payload = {'ItemId': job.media['item']['id'], 'MediaSourceId': job.media['source_id'],
            'PlaySessionId': job.media['play_session_id'], 'PositionTicks': round(position * TICKS),
            'CanSeek': True, 'IsPaused': paused, 'IsMuted': False, 'VolumeLevel': 100,
            'PlayMethod': 'DirectStream', 'AudioStreamIndex': job.media['audio_index']}
        path = '/Sessions/Playing' + {'start': '', 'progress': '/Progress', 'stop': '/Stopped'}[kind]
        self.request(path, data=payload)
