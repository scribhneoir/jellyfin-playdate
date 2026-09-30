Net = { queue = {}, active = nil, config = nil }

function Net.configure(config)
    if type(config.url) ~= 'string' or type(config.token) ~= 'string' or config.token:find('[\r\n]') then return false end
    local scheme, authority, base = config.url:match('^(https?)://([^/]+)(.*)$')
    if not scheme or authority:find('@', 1, true) or config.url:find('[%s%c]') then return false end
    local host, port = authority:match('^([^:]+):(%d+)$')
    host = host or authority
    port = tonumber(port) or (scheme == 'https' and 443 or 80)
    if port < 1 or port > 65535 or host:find('[%s?#]') or base:find('[?#]') then return false end
    local backend = config.backend or 'bridge'
    if backend ~= 'bridge' and backend ~= 'plugin' then return false end
    if backend == 'plugin' and config.token:find('[^%w_-]') then return false end
    base = base:gsub('/+$', '')
    Net.config = { host = host, port = port, ssl = scheme == 'https', backend = backend,
        base = base..(backend == 'plugin' and '/Playdate' or ''), token = config.token }
    return true
end

function Net.escape(text)
    return (tostring(text):gsub('([^%w%-_%.~])', function(c)
        return string.format('%%%02X', string.byte(c))
    end))
end

function Net.connection()
    local c = assert(Net.config, 'Server address is not configured')
    local connection, err = playdate.network.http.new(c.host, c.port, c.ssl,
        'browse your Jellyfin library, download posters, and play video')
    assert(connection, err)
    connection:setConnectTimeout(10)
    connection:setKeepAlive(false)
    connection:setReadTimeout(0)
    connection:setReadBufferSize(65536)
    return connection
end

function Net.headers()
    if Net.config.backend == 'plugin' then
        return { ['X-Emby-Authorization'] = 'MediaBrowser Client="Playdate Jellyfin", Device="Playdate", DeviceId="playdate-pds-client", Version="0.4.1", Token="'..Net.config.token..'"',
            ['Content-Type'] = 'application/json', Connection = 'close' }
    end
    return { Authorization = 'Bearer '..Net.config.token, ['Content-Type'] = 'application/json' }
end

function Net.request(method, path, body, callback, binary)
    if method == 'POST' and path:match('/progress$') then
        for _, request in ipairs(Net.queue) do
            if request.path == path then request.body=body; request.callback=callback; return end
        end
    end
    Net.queue[#Net.queue+1] = { method=method, path=path, body=body, callback=callback, binary=binary }
end

local function close(request)
    if request.connection then
        request.connection:setHeadersReadCallback(nil)
        request.connection:setRequestCompleteCallback(nil)
        request.connection:setConnectionClosedCallback(nil)
        request.connection:close()
    end
end

function Net.cancelAll()
    if Net.active then close(Net.active) end
    Net.active, Net.queue = nil, {}
end

function Net.update()
    if not Net.active and #Net.queue > 0 then
        local r = table.remove(Net.queue, 1)
        Net.active = r
        r.chunks, r.bytes = {}, 0
        r.started = playdate.getCurrentTimeMilliseconds()
        local ok, err = pcall(function()
            r.connection = Net.connection()
            r.connection:setHeadersReadCallback(function() r.status = r.connection:getResponseStatus() end)
            r.connection:setRequestCompleteCallback(function() r.done = true end)
            r.connection:setConnectionClosedCallback(function() r.done = true end)
            local sent, sendError
            if r.method == 'POST' then
                sent, sendError = r.connection:post(Net.config.base..r.path, Net.headers(), json.encode(r.body or {}))
            else
                sent, sendError = r.connection:get(Net.config.base..r.path, Net.headers())
            end
            assert(sent, sendError)
        end)
        if not ok then r.error = tostring(err) end
    end
    local r = Net.active
    if not r then return end
    local ok, err = pcall(function()
        if r.connection then
            local available = r.connection:getBytesAvailable()
            if available > 0 then
                local chunk = r.connection:read(math.min(available, 16384))
                if chunk and #chunk > 0 then
                    r.bytes = r.bytes + #chunk
                    if r.bytes > (r.binary and 8192 or 65536) then error('Server response is too large') end
                    r.chunks[#r.chunks+1] = chunk
                end
            end
            local connectionError = r.connection:getError()
            if connectionError then r.error = tostring(connectionError) end
            if r.done and r.connection:getBytesAvailable() == 0 then r.finished = true end
        end
    end)
    if not ok then r.error = tostring(err) end
    if playdate.getCurrentTimeMilliseconds()-r.started > 25000 then r.error = 'Server request timed out' end
    if r.error or r.finished then
        local data
        if not r.error then
            local raw = table.concat(r.chunks)
            if r.binary and r.status == 200 then data = raw
            else
                local decoded, value = pcall(json.decode, raw)
                if decoded and type(value) == 'table' then data = value
                else r.error = 'The server returned an unreadable response' end
            end
            if r.status and r.status >= 400 then
                r.error = type(data) == 'table' and data.error or ('Server returned HTTP '..r.status)
                if r.status == 401 or r.status == 403 then r.error = r.error..'; check your access token and permissions' end
            end
        end
        close(r)
        Net.active = nil
        if r.callback then r.callback(data, r.error) end
    end
end
