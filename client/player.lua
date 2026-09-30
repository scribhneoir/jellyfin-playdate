Player = { state = 'idle', position = 0 }
local pd, gfx = playdate, playdate.graphics

local function inspect(object, method)
    if not object or not object[method] then return end
    local ok, value, extra = pcall(object[method], object)
    if ok then return value, extra end
end

function Player.snapshot()
    local received, length = inspect(Player.connection, 'getProgress')
    local parts = Player.parts or {}
    return { milliseconds=pd.getCurrentTimeMilliseconds(), state=Player.state,
        controlPending=Net.active ~= nil,
        controlProgress=Net.active and Net.active.path:match('/progress$') ~= nil,
        part=parts.index, base=parts.base, expected=parts.expected, final=parts.final,
        responseComplete=parts.responseComplete, peerClosed=parts.peerClosed,
        receiveBuffer=Player.receiveBuffer,
        closed=Player.closed, received=received, length=length,
        available=inspect(Player.connection, 'getBytesAvailable'),
        networkError=inspect(Player.connection, 'getError'),
        consumed=inspect(Player.stream, 'getBytesRead'),
        buffered=inspect(Player.stream, 'getBufferedFrameCount'),
        frame=inspect(Player.video, 'getCurrentFrame'),
        audioPlaying=inspect(Player.audio, 'isPlaying'),
        audioUnderrun=inspect(Player.audio, 'didUnderrun'),
        audioOffset=inspect(Player.audio, 'getOffset') }
end

local function trace(event)
    Player.history = Player.history or {}
    local row = Player.snapshot()
    row.event = event
    Player.history[#Player.history+1] = row
    if #Player.history > 64 then table.remove(Player.history, 1) end
end

local function release()
    if Player.audio then
        Player.audio:setFinishCallback(nil)
        Player.audio:stop()
        if Player.channel then Player.channel:removeSource(Player.audio) end
    end
    if Player.channel then Player.channel:remove() end
    Player.audio, Player.video, Player.stream, Player.channel, Player.context = nil, nil, nil, nil, nil
    collectgarbage('collect')
    if Player.connection then
        Player.connection:setHeadersReadCallback(nil)
        Player.connection:setRequestCompleteCallback(nil)
        Player.connection:setConnectionClosedCallback(nil)
        Player.connection:close()
        Player.connection = nil
    end
    Player.parts = nil
    pd.setAutoLockDisabled(false)
end

function Player.time()
    if Player.video and Player.session then
        local frame = Player.video:getCurrentFrame()
        if frame >= 0 then
            Player.position = math.min(Player.session.duration, Player.session.start + frame / Player.session.fps)
        end
    end
    return Player.position
end

local function stopped(callback)
    local position = Player.time()
    local started = Player.started == true
    local session = Player.session
    Player.session = nil
    release()
    if session then
        Net.request('POST', '/api/session/'..session.id..'/stop', {position=position, started=started}, function(data, err)
            if err or (data and data.progressWarning ~= '') then
                Player.progressWarning = err or data.progressWarning
            end
            if callback then callback() end
        end)
    elseif callback then callback() end
end

function Player.fail(message)
    if Player.state == 'error' then return end
    trace('error')
    Player.lastDiagnostic = {error=message, history=Player.history}
    pcall(pd.datastore.write, Player.lastDiagnostic, 'playback-diagnostic')
    Player.error = message
    Player.state = 'error'
    stopped()
end

function Player.stop(callback)
    Player.generation = (Player.generation or 0) + 1
    Player.state = 'idle'
    stopped(callback)
    Player.frame = nil
end

local function attachStream()
    Player.stream = assert(gfx.videostream.new(Player.connection), 'Native PDS playback is unavailable')
    Player.video, Player.audio = Player.stream:getVideoPlayer(), Player.stream:getFilePlayer()
    -- Keep video pixels separate from controls, including while the stream is paused.
    Player.context = gfx.image.new(400, 240, gfx.kColorBlack)
    Player.video:setContext(Player.context)
    Player.channel = pd.sound.channel.new()
    Player.channel:setVolume(1)
    Player.channel:addSource(Player.audio)
    Player.audio:setVolume(1)
    Player.audio:setStopOnUnderrun(false)
    local headphones = pd.sound.getHeadphoneState()
    pd.sound.setOutputsActive(headphones, not headphones)
end

local function getPart()
    local parts = Player.parts
    parts.base = Player.stream:getBytesRead()
    parts.expected, parts.nextAt, parts.final = nil, nil, false
    parts.responseComplete, parts.peerClosed = false, false
    local path = Net.config.base..parts.path..'/'..parts.index
    if Player.started and Player.session.progressInParts then
        path = path..'?position='..Net.escape(Player.time())
    end
    local ok, err = Player.connection:get(path, Net.headers())
    assert(ok, err)
    trace('request')
end

local function connect(session)
    Player.session = session
    Player.position = session.start
    Player.closed, Player.headers, Player.pendingError = false, false, nil
    Player.started = false
    Player.lastFrame, Player.lastAdvance = -1, pd.getCurrentTimeMilliseconds()
    Player.lastProgress = Player.lastAdvance
    Player.history, Player.lastDiagnostic, Player.lastTrace = {}, nil, Player.lastAdvance
    Player.statusPending = false
    Player.parts = session.parts and {path=session.parts,index=0} or nil
    local maxBytes = session.maxSegmentBytes or 65536
    assert(type(maxBytes) == 'number' and maxBytes > 0 and maxBytes <= 2097152 and maxBytes % 1 == 0,
        'Invalid video segment limit')
    Player.connection = Net.connection()
    -- Large segments can arrive in a burst. Reserve room for the entire
    -- advertised segment while the native decoder drains the socket.
    Player.receiveBuffer = Player.parts and maxBytes or 65536
    Player.connection:setReadBufferSize(Player.receiveBuffer)
    Player.connection:setHeadersReadCallback(function()
        local status = Player.connection:getResponseStatus()
        if status ~= 200 then Player.pendingError = 'Cannot start video (HTTP '..tostring(status)..')'; return end
        Player.headers = true
        if Player.parts then
            local parts = Player.parts
            local _, size = Player.connection:getProgress()
            if not size or size <= 0 or size > maxBytes then Player.pendingError = 'Invalid video segment length'; return end
            parts.expected = parts.base+size
            if session.progressInParts then Player.progressWarning = nil end
            for key,value in pairs(Player.connection:getResponseHeaders() or {}) do
                if key:lower() == 'x-pds-final' and value == '1' then parts.final=true end
                if key:lower() == 'x-pds-progress-warning' then Player.progressWarning=value end
            end
            trace('headers')
        end
    end)
    Player.connection:setRequestCompleteCallback(function()
        if Player.parts then Player.parts.responseComplete=true; trace('response-complete') end
    end)
    Player.connection:setConnectionClosedCallback(function()
        if Player.parts then
            if Player.parts.expected then Player.parts.peerClosed=true; trace('peer-closed') end
        else Player.closed=true end
    end)
    attachStream()
    if Player.parts then getPart()
    else
        local ok, err = Player.connection:get(Net.config.base..session.path, Net.headers())
        assert(ok, err)
    end
    pd.setAutoLockDisabled(true)
end

local function begin(item, position)
    Player.state, Player.item, Player.position = 'loading', item, position
    Player.started = false
    Player.error, Player.progressWarning = nil, nil
    Player.generation = (Player.generation or 0) + 1
    local generation = Player.generation
    local body = {id=item.id, position=position}
    if Net.config.backend == 'plugin' then body.segmentSeconds=8 end
    Net.request('POST', '/api/play', body, function(data, err)
        if generation ~= Player.generation then
            if data and data.id then Net.request('POST', '/api/session/'..data.id..'/stop', {position=position}) end
            return
        end
        if err then Player.fail(err); return end
        if Net.config.backend == 'plugin' and (not data.progressInParts or data.segmentSeconds ~= 8) then
            Player.session = data
            Player.fail('Update the Jellyfin Playdate plugin to 0.4.0 or newer.'); return
        end
        -- Net callbacks execute inside playdate.update, where network permission
        -- dialogs and their coroutine yield are supported.
        local ok, failure = pcall(connect, data)
        if not ok then Player.fail(tostring(failure)) end
    end)
end

function Player.play(item, position)
    if not Player.item or Player.item.id ~= item.id then Player.frame = nil end
    Player.item = item
    if not gfx.videostream then Player.fail('This Playdate OS does not support native streaming'); return end
    begin(item, position or 0)
end

function Player.pause()
    if Player.state == 'paused' then
        begin(Player.item, Player.position)
    elseif Player.state == 'playing' or Player.state == 'buffering' then
        Player.state = 'pausing'
        stopped(function() if Player.state == 'pausing' then Player.state = 'paused' end end)
    end
end

function Player.seek(position)
    if not Player.item then return end
    position = math.max(0, math.min(position, math.max(0, Player.item.duration - 1)))
    Player.generation = (Player.generation or 0) + 1
    Player.state = 'seeking'
    stopped(function() if Player.state == 'seeking' then begin(Player.item, position) end end)
end

function Player.update()
    if not Player.stream or Player.state == 'error' then return end
    if Player.pendingError then Player.fail(Player.pendingError); return end
    local ok, err = pcall(function()
        Player.stream:update()
        local parts = Player.parts
        if parts then
            -- The decoder can finish a response before Lua's completion callback.
            -- Wait until the decoder has consumed the body before the next GET.
            -- Reset HTTP/TCP state between responses once their bodies are drained.
            if parts.expected and Player.stream:getBytesRead() >= parts.expected then
                assert(Player.stream:getBytesRead() == parts.expected, 'Video segment length mismatch')
                trace('consumed')
                parts.expected=nil
                Player.connection:close()
                if parts.final then Player.closed=true
                else parts.nextAt=pd.getCurrentTimeMilliseconds()+50 end
            end
            if parts.nextAt and pd.getCurrentTimeMilliseconds() >= parts.nextAt then
                parts.index=parts.index+1
                getPart()
            end
        end
        local buffered = Player.stream:getBufferedFrameCount()
        local startupFrames = Player.session.segmentSeconds == 8 and 45 or 15
        if not Player.started and Player.headers and (buffered >= startupFrames or (Player.closed and buffered > 0)) then
            local started, reason = Player.audio:play()
            if started then
                Player.started = true
                Player.state = 'playing'
                Player.lastProgress = pd.getCurrentTimeMilliseconds()-10001
            elseif reason then error(reason) end
        end
        local now = pd.getCurrentTimeMilliseconds()
        if now-Player.lastTrace >= 1000 then Player.lastTrace=now; trace('sample') end
        local frame = Player.video:getCurrentFrame()
        if frame ~= Player.lastFrame then
            Player.lastFrame, Player.lastAdvance = frame, now
            if frame >= 0 then Player.frame = Player.context end
            if Player.started then Player.state = 'playing' end
        elseif Player.started and now-Player.lastAdvance > 800 then
            Player.state = 'buffering'
        end
        local position = Player.time()
        if Player.started and not (parts and Player.session.progressInParts) and now-Player.lastProgress > 10000 then
            Player.lastProgress = now
            Net.request('POST', '/api/session/'..Player.session.id..'/progress', {position=position}, function(data, failure)
                if failure then Player.progressWarning = failure
                elseif data and data.progressWarning ~= '' then Player.progressWarning = data.progressWarning
                else Player.progressWarning = nil end
            end)
        end
        if Player.closed and buffered == 0 and now-Player.lastAdvance > 350 and not Player.statusPending then
            Player.statusPending = true
            local session = Player.session
            Net.request('GET', '/api/session/'..session.id, nil, function(data, failure)
                if Player.session ~= session then return end
                if failure then Player.fail(failure)
                elseif data.state == 'complete' and Player.time() >= session.duration-1 then
                    -- Stop reporting uses the actual last decoded frame; the
                    -- completion threshold does not fabricate a watched interval.
                    Player.state = 'ended'
                    stopped()
                else Player.fail(data.error ~= '' and data.error or 'Stream interrupted. A: resume from this point.') end
            end)
        end
        if now-Player.lastAdvance > 30000 then error('Video stopped arriving. A: retry from this point.') end
        local networkError = Player.connection:getError()
        if networkError then error(networkError) end
    end)
    if not ok then Player.fail(tostring(err)) end
end

function Player.shutdown()
    release()
    Player.frame = nil
end
