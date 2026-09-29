import 'CoreLibs/graphics'
import 'config'
import 'net'
import 'posters'
import 'player'
import 'ui'

local pd, gfx = playdate, playdate.graphics
local started = pd.getCurrentTimeMilliseconds()
local phase, item, poster, lastState, result = 'start'
local transitions = {}
local fullBytes, finalFrame = 0, -1
local lastTrace = 0
local trace = assert(pd.file.open('trace.log', pd.file.kFileWrite))
local function log(value) trace:write(value..'\n'); trace:flush() end
local function finish(ok, message) result={ok=ok,message=message,transitions=transitions} end
local function fail(err) if err then error(err) end end
function pd.update()
    local ok, err = pcall(function()
        if phase == 'start' then
            assert(Net.configure(CLIENT_CONFIG))
            Posters.configure(CLIENT_CONFIG)
            -- Force one download on every run, then verify the cached load.
            for _, name in ipairs(pd.file.listFiles('posters/') or {}) do pd.file.delete('posters/'..name) end
            Net.request('GET','/api/items?search=Diagnostic',nil,function(data, failure)
                fail(failure)
                log('metadata received')
                for _, entry in ipairs(data.items) do if entry.name=='Diagnostic' then item=entry end end
                assert(item and item.poster ~= '', 'Missing test poster metadata')
                Posters.load(item,function(image) poster=image;log('poster received') end)
                phase='poster'
            end)
            phase='metadata'
        end
        Net.update()
        local now = pd.getCurrentTimeMilliseconds()
        if Net.active and now-lastTrace > 5000 then
            local r=Net.active
            log('HTTP '..r.method..' status='..tostring(r.status)..' bytes='..r.bytes..' done='..tostring(r.done))
            lastTrace=now
        end
        if Player.stream and now-lastTrace > 3000 then
            log('video available='..Player.connection:getBytesAvailable()..' read='..Player.stream:getBytesRead()..
                ' frames='..Player.stream:getBufferedFrameCount()..' closed='..tostring(Player.closed))
            lastTrace=now
        end
        Player.update()
        if phase=='full' and Player.stream then
            fullBytes=Player.stream:getBytesRead()
            finalFrame=Player.video:getCurrentFrame()
        end
        if Player.state ~= lastState then
            transitions[#transitions+1]={state=Player.state,phase=phase,position=Player.time()}
            lastState=Player.state
            log(phase..': '..Player.state..' at '..Player.time())
        end
        if Player.state == 'error' then error(Player.error) end
        if phase == 'poster' and poster then
            local w,h=poster:getSize()
            assert(w==96 and h==144, 'Wrong native PDI dimensions')
            for y=0,143 do for x=0,95 do
                local expected = ((x//3+y//5)%2==1) and gfx.kColorWhite or gfx.kColorBlack
                assert(poster:sample(x,y)==expected, 'PDI pixel mismatch')
            end end
            local cached
            Posters.load(item,function(image) cached=image end)
            assert(cached and #Net.queue==0, 'Poster cache did not avoid HTTP')
            UI.browser({kind='detail',title=item.name,item=item,poster=poster,selected=1,
                rows={{label='Play from beginning',icon='play'}}})
            log('PDI: 13824 pixels match; cache passed')
            phase='preview'
            started = pd.getCurrentTimeMilliseconds()
        elseif phase == 'preview' and pd.getCurrentTimeMilliseconds()-started > 600 then
            Player.play(item,0); phase='initial'
        elseif phase == 'initial' and Player.state=='playing' and Player.time()>=.4 then
            assert(Player.frame, 'Missing decoded video image')
            local black,white=false,false
            for y=0,239,10 do for x=0,399,10 do
                local pixel=Player.frame:sample(x,y)
                black=black or pixel==gfx.kColorBlack; white=white or pixel==gfx.kColorWhite
            end end
            assert(black and white,'Decoded video image is blank')
            Player.pause();phase='pause'
        elseif phase == 'pause' and Player.state=='paused' then
            assert(Player.time()>=.4 and Player.time()<1.5)
            Player.pause();phase='resume'
        elseif phase == 'resume' and Player.state=='playing' and Player.time()>=.8 then
            Player.seek(1.8);phase='seek'
        elseif phase == 'seek' and Player.state=='playing' and Player.time()>=2 then
            assert(Player.time()<3);Player.stop();phase='stop'
        elseif phase == 'stop' and Player.state=='idle' and not Net.active and #Net.queue==0 then
            assert(not Player.progressWarning,Player.progressWarning)
            Player.play(item,0);phase='full'
        elseif phase == 'full' and Player.state=='ended' and not Net.active and #Net.queue==0 then
            assert(not Player.progressWarning,Player.progressWarning)
            assert(Player.time()>=item.duration-.2, 'Full playback ended too early')
            finish(true,'Native PDI pixels/cache, video pixels, play, pause, resume, seek, stop, and complete segmented playback passed')
            result.bytes=fullBytes;result.finalFrame=finalFrame
        end
        if Player.item then UI.player(Player, nil, now+3000, now) end
        if pd.getCurrentTimeMilliseconds()-started > 90000 then error('Timeout in '..phase) end
    end)
    if not ok then finish(false,tostring(err)) end
    if result then
        local f=assert(pd.file.open('result.json',pd.file.kFileWrite));f:write(json.encode(result));f:close()
        Player.shutdown();Net.cancelAll();trace:close();pd.simulator.exit()
    end
end
