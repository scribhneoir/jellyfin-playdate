Posters = {}
local pd, gfx = playdate, playdate.graphics
local directory = 'posters/'
local connection

function Posters.configure(config)
    local identity = { url=config.url, backend=config.backend or 'bridge', token=config.token }
    local old = pd.datastore.read('poster-connection')
    if not old or old.url ~= identity.url or old.backend ~= identity.backend or old.token ~= identity.token then
        for _, name in ipairs(pd.file.listFiles(directory) or {}) do pd.file.delete(directory..name) end
        pd.datastore.write(identity, 'poster-connection')
    end
    connection = identity
    pd.file.mkdir(directory)
end

function Posters.load(item, callback)
    if not connection or connection.backend ~= 'plugin' or not item.poster or item.poster == '' then return end
    if not item.id:match('^%x+$') or not item.poster:match('^%x+$') then return end
    local path = directory..item.id..'-'..item.poster..'.pdi'
    if pd.file.exists(path) then
        local image = gfx.image.new(path)
        if image then callback(image); return end
        pd.file.delete(path)
    end
    local activeConnection = connection
    Net.request('GET', '/api/items/'..item.id..'/poster.pdi', nil, function(data, err)
        if err or connection ~= activeConnection or type(data) ~= 'string' then return end
        -- Validate the fixed 96x144 opaque profile before using the native decoder.
        if #data ~= 1760 or data:sub(1,12) ~= 'Playdate IMG' or
            data:sub(13,32) ~= string.pack('<I4I2I2I2I2I2I2I2I2', 0,96,144,12,0,0,0,0,4) then return end
        local files = pd.file.listFiles(directory) or {}
        table.sort(files)
        while #files >= 64 do pd.file.delete(directory..table.remove(files, 1)) end
        local file = pd.file.open(path, pd.file.kFileWrite)
        if not file then return end
        local written = file:write(data)
        file:close()
        if written ~= #data then pd.file.delete(path); return end
        local image = gfx.image.new(path)
        if image then callback(image) else pd.file.delete(path) end
    end, true)
end
