import 'CoreLibs/graphics'
import 'config'
import 'net'
import 'player'
import 'ui'

local pd = playdate
local started = pd.getCurrentTimeMilliseconds()
local phase, lastState, transitions = 'start', '', {}
local trace = assert(pd.file.open('trace.log', pd.file.kFileWrite))
local function log(value) trace:write(tostring(value)..'\n'); trace:flush() end
log('boot')
local result
local heldFrame
local item = { id=string.rep('1', 32), duration=3, name='Generated diagnostic' }
local function finish(ok, message)
    result = {ok=ok, message=message, transitions=transitions}
end
local function exit()
    log('finish: '..tostring(result.ok)..' '..result.message)
    local output = assert(pd.file.open('result.json', pd.file.kFileWrite))
    output:write(json.encode(result)); output:close()
    Player.shutdown()
    Net.cancelAll()
    trace:close()
    pd.simulator.exit()
end

function pd.update()
    local ok, err = pcall(function()
        if phase == 'start' then
            assert(Net.configure(CLIENT_CONFIG))
            Player.play(item, 0)
            phase = 'initial'
        end
        Net.update()
        Player.update()
        local now = pd.getCurrentTimeMilliseconds()
        if Player.state ~= lastState then
            transitions[#transitions+1] = {state=Player.state, phase=phase, position=Player.time(), milliseconds=now-started}
            lastState=Player.state
            log(phase..': '..Player.state..' at '..Player.time())
        end
        if Player.state == 'error' then error(Player.error) end
        if phase == 'initial' and Player.state == 'playing' and Player.time() >= .4 then
            heldFrame=assert(Player.frame, 'No decoded frame to hold')
            local black, white=false, false
            for y=0,239,10 do for x=0,399,10 do
                local pixel=heldFrame:sample(x, y)
                black=black or pixel==pd.graphics.kColorBlack
                white=white or pixel==pd.graphics.kColorWhite
            end end
            assert(black and white, 'Decoder context contains no video detail')
            log('pause request')
            Player.pause()
            log('pause returned')
            phase='pause'
        elseif phase == 'pause' and Player.state == 'paused' then
            assert(Player.time() >= .4 and Player.time() < 1.5, 'pause position was lost')
            assert(Player.frame==heldFrame, 'Pause discarded the last video frame')
            UI.player(Player, nil, now+3000, now)
            if pd.argv[2] then pd.simulator.writeToFile(pd.graphics.getWorkingImage(), pd.argv[2]) end
            log('resume request')
            Player.pause()
            assert(Player.frame==heldFrame, 'Resume discarded the held frame before new video arrived')
            log('resume returned')
            phase='resume'
        elseif phase == 'resume' and Player.state == 'playing' and Player.time() >= .8 then
            UI.player(Player, nil, 0, now)
            local shown=pd.graphics.getWorkingImage()
            for y=0,239,10 do for x=0,399,10 do
                assert(shown:sample(x,y)==Player.frame:sample(x,y), 'Faded controls left marks on the video')
            end end
            log('seek request')
            Player.seek(1.8)
            log('seek returned')
            phase='seek'
        elseif phase == 'seek' and Player.state == 'playing' and Player.time() >= 2 then
            assert(Player.time() < 3, 'seek jumped beyond the end')
            log('stop request')
            Player.stop()
            assert(not Player.frame, 'Stop retained the previous title frame')
            log('stop returned')
            phase='stop'
        elseif phase == 'stop' and Player.state == 'idle' and not Net.active and #Net.queue == 0 then
            assert(not Player.progressWarning, Player.progressWarning)
            finish(true, 'Native play, held-frame pause, resume, overlay fade, seek, and stop passed')
            return
        end
        if now-started > 90000 then error('Native smoke test timed out in '..phase) end
        UI.player(Player, nil, now+3000, now)
    end)
    if not ok then finish(false, tostring(err)) end
    if result then exit() end
end
