import 'CoreLibs/graphics'
import 'CoreLibs/keyboard'
import 'config'
import 'net'
import 'posters'
import 'player'
import 'ui'

local pd = playdate
local config = pd.datastore.read('connection') or CLIENT_CONFIG
local screen, stack, navigation = {kind='boot'}, {}, 0
local pending, keyboardOpen, crankRemainder = nil, false, 0
local seekTarget, seekAt, overlayUntil
local logFile = pd.file.open('client-events.log', pd.file.kFileWrite)
local function log(message)
    if logFile then logFile:write(message..'\n'); logFile:flush() end
end
local function clock() return pd.getCurrentTimeMilliseconds() end
local timeText = UI.time
local function push(nextScreen)
    if screen.kind ~= 'boot' then stack[#stack+1] = screen end
    screen = nextScreen
end
local function errorScreen(message, retry)
    log('error: '..tostring(message))
    screen = {kind='error', error=message, retry=retry}
end
local home, loadList, detail, settingsScreen
local function back()
    navigation = navigation + 1
    if #stack > 0 then screen = table.remove(stack)
    else home() end
end

local function keyboard(value, callback)
    keyboardOpen = true
    local accepted = false
    pd.keyboard.keyboardWillHideCallback = function(ok) accepted = ok end
    pd.keyboard.keyboardDidHideCallback = function()
        keyboardOpen = false
        local result = pd.keyboard.text
        pd.keyboard.keyboardWillHideCallback = nil
        pd.keyboard.keyboardDidHideCallback = nil
        if accepted then pending = function() callback(result) end end
    end
    pd.keyboard.show(value or '')
end

local function saveConnection()
    if not Net.configure(config) then errorScreen('Enter your server URL and a valid access token.', settingsScreen); return end
    pd.datastore.write(config, 'connection')
    Net.cancelAll()
    home()
end

settingsScreen = function()
    screen = {kind='list', title='Connection', subtitle='Your Jellyfin server', selected=1, rows={
        {label='Connection: '..((config.backend or 'bridge') == 'plugin' and 'Jellyfin plugin' or 'Bridge'), action=function()
            config.backend = (config.backend or 'bridge') == 'plugin' and 'bridge' or 'plugin'; settingsScreen()
        end},
        {label='Server address', note=config.url, action=function() keyboard(config.url, function(value) config.url=value; settingsScreen() end) end},
        {label='Access token: '..((config.token or '') ~= '' and 'saved' or 'not set'),
            action=function() keyboard('', function(value) config.token=value; settingsScreen() end) end},
        {label='Connect', action=saveConnection},
        {label='Use bundled connection', action=function()
            config = {url=CLIENT_CONFIG.url, token=CLIENT_CONFIG.token, backend=CLIENT_CONFIG.backend}; saveConnection()
        end},
    }}
end

home = function()
    navigation = navigation + 1
    local generation = navigation
    stack = {}
    if not Net.configure(config) or not config.token or config.token == '' then settingsScreen(); return end
    Posters.configure(config)
    screen = {kind='loading', title='Jellyfin', message='Connecting to your library...'}
    Net.request('GET', '/api/status', nil, function(status, err)
        if generation ~= navigation then return end
        if err then errorScreen(err, home); return end
        Net.request('GET', '/api/libraries', nil, function(data, failure)
            if generation ~= navigation then return end
            if failure then errorScreen(failure, home); return end
            local rows = {
                {label='Continue watching', icon='resume', action=function() loadList('view=resume', 'Continue watching', 0, true) end},
                {label='Search', icon='search', action=function() keyboard('', function(value)
                    if value:match('%S') then loadList('search='..Net.escape(value), 'Search: '..value, 0, true) end
                end) end},
            }
            for _, item in ipairs(data.items) do
                local library = item
                rows[#rows+1] = {label=library.name, icon='library', action=function() loadList('parent='..library.id, library.name, 0, true) end}
            end
            rows[#rows+1] = {label='Connection settings', action=function() push(screen); settingsScreen() end}
            screen = {kind='list', title='Jellyfin', subtitle=status.server, selected=1, rows=rows, home=true}
            log('home: '..#data.items..' libraries')
        end)
    end)
end

local function itemLabel(item)
    if item.type == 'Episode' then
        return string.format('%sE%02d  %s', item.season and ('S'..string.format('%02d', item.season)) or '',
            item.episode or 0, item.name)
    end
    return item.name
end

loadList = function(query, title, start, remember, folder)
    if remember then stack[#stack+1] = screen end
    navigation = navigation + 1
    local generation = navigation
    screen = {kind='loading', title=title, message='Loading...'}
    Net.request('GET', '/api/items?'..query..'&start='..start, nil, function(data, err)
        if generation ~= navigation then return end
        if err then errorScreen(err, function() loadList(query, title, start, false, folder) end); return end
        local rows = {}
        for _, item in ipairs(data.items) do
            local entry = item
            rows[#rows+1] = {label=itemLabel(entry), item=entry, action=function()
                if entry.folder then loadList('parent='..entry.id, entry.name, 0, true, entry)
                else detail(entry, true) end
            end}
        end
        screen = {kind='list', title=title, selected=1, rows=rows,
            subtitle=data.total..' titles', query=query, start=start, total=data.total, folder=folder}
        local list = screen
        if folder then Posters.load(folder, function(image) list.poster = image end) end
        log('list: '..#rows..' items, offset '..start)
    end)
end

local function play(item, position)
    stack[#stack+1] = screen
    screen = {kind='player'}
    seekTarget = nil
    overlayUntil = clock()+3000
    Player.play(item, position)
    log('play requested at '..timeText(position))
end

detail = function(item, remember)
    if remember then stack[#stack+1] = screen end
    navigation = navigation + 1
    local generation = navigation
    screen = {kind='loading', title=item.name, message='Loading video details...'}
    Net.request('GET', '/api/items/'..item.id, nil, function(data, err)
        if generation ~= navigation then return end
        if err then errorScreen(err, function() detail(item, false) end); return end
        local rows = {}
        if data.resume > 2 and data.resume < data.duration-1 then
            rows[#rows+1] = {label='Resume at '..timeText(data.resume), icon='resume', action=function() play(data, data.resume) end}
        end
        rows[#rows+1] = {label='Play from beginning', icon='play', action=function() play(data, 0) end}
        screen = {kind='detail', title=data.name, item=data, rows=rows, selected=1}
        local details = screen
        Posters.load(data, function(image) details.poster = image end)
        log('detail loaded')
    end)
end

local function moveSelection(amount)
    if not screen.rows or #screen.rows == 0 then return end
    screen.selected = math.max(1, math.min(#screen.rows, screen.selected+amount))
end

local previousPlayerState
local function updatePlayer()
    Player.update()
    if Player.state ~= previousPlayerState then log('player: '..Player.state); previousPlayerState=Player.state end
    local state = Player.state
    if pd.buttonJustPressed(pd.kButtonB) then
        local item, position = Player.item, Player.time()
        Player.stop()
        seekTarget = nil
        if #stack > 0 then screen=table.remove(stack) else home() end
        if item then item.resume=position; detail(item, false) end
        return
    end
    if pd.buttonJustPressed(pd.kButtonA) then
        if seekTarget then Player.seek(seekTarget); seekTarget=nil
        elseif state == 'error' then Player.play(Player.item, Player.time())
        elseif state == 'ended' then Player.play(Player.item, 0)
        else Player.pause() end
        overlayUntil=clock()+3000
    end
    if state == 'playing' or state == 'buffering' or state == 'paused' then
        local change = pd.getCrankChange()/6
        if pd.buttonJustPressed(pd.kButtonLeft) then change=change-10 end
        if pd.buttonJustPressed(pd.kButtonRight) then change=change+10 end
        if math.abs(change) > .05 then
            seekTarget=math.max(0, math.min((seekTarget or Player.time())+change, math.max(0, Player.item.duration-1)))
            seekAt=clock()
            overlayUntil=clock()+3000
        end
    end
    if seekTarget and clock()-seekAt > 700 then Player.seek(seekTarget); seekTarget=nil end
    UI.player(Player, seekTarget, overlayUntil, clock())
end

pd.display.setRefreshRate(30)
pd.getSystemMenu():addMenuItem('Connection', function()
    pending=function()
        Player.stop()
        navigation=navigation+1
        stack={}
        settingsScreen()
    end
end)
local booted=false
function pd.update()
    if not booted then booted=true; home() end
    if pending then local action=pending; pending=nil; action() end
    Net.update()
    if keyboardOpen or pd.keyboard.isVisible() then return end
    if screen.kind == 'player' then updatePlayer(); return end
    if pd.buttonJustPressed(pd.kButtonB) then back(); return end
    if pd.buttonJustPressed(pd.kButtonUp) then moveSelection(-1) end
    if pd.buttonJustPressed(pd.kButtonDown) then moveSelection(1) end
    crankRemainder=crankRemainder+pd.getCrankChange()
    local ticks=math.floor(math.abs(crankRemainder)/15)
    if ticks > 0 then
        local direction=crankRemainder > 0 and 1 or -1
        moveSelection(ticks*direction)
        crankRemainder=crankRemainder-ticks*direction*15
    end
    if screen.query then
        if pd.buttonJustPressed(pd.kButtonLeft) and screen.start > 0 then loadList(screen.query, screen.title, math.max(0, screen.start-20), false, screen.folder)
        elseif pd.buttonJustPressed(pd.kButtonRight) and screen.start+20 < screen.total then loadList(screen.query, screen.title, screen.start+20, false, screen.folder) end
    end
    if pd.buttonJustPressed(pd.kButtonA) then
        if screen.kind == 'error' and screen.retry then screen.retry()
        elseif screen.rows and screen.rows[screen.selected] then screen.rows[screen.selected].action() end
    end
    UI.browser(screen)
end

function pd.gameWillTerminate()
    -- Network callbacks cannot be awaited while the runtime is terminating.
    -- Periodic progress has already saved the most recent decoded position.
    Player.shutdown()
    Net.cancelAll()
    if logFile then logFile:close(); logFile=nil end
end

function pd.serialMessageReceived(message)
    if message == 'jf-status' then
        print(json.encode(Player.lastDiagnostic or {current=Player.snapshot(), history=Player.history}))
    end
end
