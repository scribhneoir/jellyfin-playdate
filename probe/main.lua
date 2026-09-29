import 'CoreLibs/graphics'
import 'config'

local pd, gfx = playdate, playdate.graphics
local options = PDS_PROBE_CONFIG
for _, arg in ipairs(pd.argv or {}) do
    local key, value = arg:match('^([%w_]+)=(.*)$')
    if key then options[key] = value end
end
local runSeconds = tonumber(options.seconds) or 20
local stream, audio, video, connection, mixer
local ready, finished, paused, failed = false, false, false, false
local startedAt = pd.getCurrentTimeMilliseconds()
local lastFrame, lastPulse, lastRow = -1, false, -1
local eventLog = assert(pd.file.open('events.tsv', pd.file.kFileWrite))
eventLog:write('seconds\tevent\tframe\tbuffered\tbytes\tlua_kib\tdetail\n')
eventLog:flush()
-- Optional control tone tests Simulator audio routing independently of PDS.
local controlTone
if options.control_tone == '1' then
    controlTone = pd.sound.synth.new(pd.sound.kWaveSine)
    controlTone:playNote(440, 0.3, 0.25)
end

local function log(event, detail)
    local t = (pd.getCurrentTimeMilliseconds() - startedAt) / 1000
    local frame = video and video:getCurrentFrame() or -1
    local buffered = stream and stream:getBufferedFrameCount() or 0
    local bytes = stream and stream:getBytesRead() or 0
    detail = tostring(detail or ''):gsub('[\r\n\t]', ' ')
    eventLog:write(string.format('%.3f\t%s\t%d\t%d\t%d\t%.1f\t%s\n',
        t, event, frame, buffered, bytes, collectgarbage('count'), detail))
    eventLog:flush()
end

local function release()
    if audio then
        audio:setFinishCallback(nil)
        audio:stop()
        if mixer then mixer:removeSource(audio) end
    end
    if mixer then mixer:remove() end
    if controlTone then controlTone:stop(); controlTone = nil end
    audio, video, stream, mixer = nil, nil, nil, nil
    collectgarbage('collect') -- release the stream before closing its transport
    if connection then
        connection:setHeadersReadCallback(nil)
        connection:setConnectionClosedCallback(nil)
        connection:close()
        connection = nil
    end
end

local function fail(message)
    if failed or finished then return end
    log('error', message)
    failed = true
end

local function bind(source)
    stream = assert(gfx.videostream.new(source))
    video = stream:getVideoPlayer()
    audio = stream:getFilePlayer()
    -- Route the borrowed fileplayer through an explicitly configured channel.
    mixer = pd.sound.channel.new()
    mixer:setVolume(1)
    log('audio_channel', mixer:addSource(audio))
    local headphones = pd.sound.getHeadphoneState()
    pd.sound.setOutputsActive(headphones, not headphones)
    video:useScreenContext()
    -- Volume zero changes native buffer consumption in SDK 3.1.1. For silent
    -- automated tests route this process to an isolated audio sink instead.
    audio:setVolume(1)
    audio:setStopOnUnderrun(true)
    audio:setFinishCallback(function(player)
        log(player:didUnderrun() and 'underrun' or 'audio_finished')
        ready = false
    end)
end

local function start()
    assert(gfx.videostream, 'Native videostream API is unavailable')
    if options.host then
        connection = assert(pd.network.http.new(options.host, tonumber(options.port) or 8000,
            options.https == '1', 'play the local PDS diagnostic stream'))
        connection:setConnectTimeout(10)
        connection:setReadTimeout(0)
        connection:setReadBufferSize(65536)
        connection:setHeadersReadCallback(function()
            local status = connection:getResponseStatus()
            log('headers', status)
            if status ~= 200 then fail('HTTP '..tostring(status)) end
        end)
        connection:setConnectionClosedCallback(function() log('connection_closed') end)
        bind(connection)
        local ok, err = connection:get(options.path or '/stream.pds')
        assert(ok, err)
        log('opened', 'http')
    else
        bind(options.file or 'fixture.pds')
        log('opened', 'file')
    end
end

local function finish()
    if finished then return end
    log('finished')
    finished = true
    if pd.isSimulator and options.capture and video then
        pd.simulator.writeToFile(gfx.getDisplayImage(), options.capture)
    end
    release()
    eventLog:close()
    pd.setAutoLockDisabled(false)
    if pd.isSimulator then pd.simulator.exit() end
end

pd.setAutoLockDisabled(true)
pd.display.setRefreshRate(30)
local initialized = false
function pd.update()
    -- Manual mode gives a person time to bring the Simulator to the foreground
    -- before the short diagnostic begins. Restart the app to repeat the clip.
    if not initialized and options.manual == '1' then
        gfx.clear(gfx.kColorWhite)
        gfx.drawText('Native PDS diagnostic\n\nA: start the 12-second clip\nB: exit\n\nWatch for a white flash and\nlisten for a beep each second.', 20, 30)
        if pd.buttonJustPressed(pd.kButtonB) then finish(); return end
        if not pd.buttonJustPressed(pd.kButtonA) then return end
        startedAt = pd.getCurrentTimeMilliseconds()
    end
    -- Network permissions must be requested from update/startup, not a button callback.
    if not initialized then
        initialized = true
        local ok, err = pcall(start)
        if not ok then fail(err) end
    end
    local t = (pd.getCurrentTimeMilliseconds() - startedAt) / 1000
    if pd.buttonJustPressed(pd.kButtonB) or (runSeconds > 0 and t >= runSeconds) then finish(); return end
    if failed or finished then
        if failed and stream then release() end
        gfx.clear(gfx.kColorWhite)
        gfx.drawText('Probe stopped. See events.tsv.\nB: exit', 10, 90)
        return
    end
    if pd.buttonJustPressed(pd.kButtonA) and ready then
        paused = not paused
        audio:setPaused(paused)
        log(paused and 'paused' or 'resumed')
    end
    local ok, err = pcall(function()
        if not paused then stream:update() end
        if not ready and not paused and stream:getBufferedFrameCount() >= 3 then
            local playing, playError = audio:play()
            if playing then ready = true; log('audio_started')
            elseif playError then log('audio_wait', playError) end
        end
        local frame = video:getCurrentFrame()
        if ready and stream:getBytesRead() > 0 and frame ~= lastFrame then
            log(lastFrame < 0 and 'first_frame' or 'frame')
            lastFrame = frame
            -- The generated diagnostic clip has a flash in this region each second.
            local pulse = gfx.getDisplayImage():sample(40, 40) == gfx.kColorWhite
            if pulse and not lastPulse then log('flash') end
            lastPulse = pulse
        end
        if math.floor(t) ~= lastRow then
            log('status', audio:isPlaying() and 'playing' or 'waiting')
            lastRow = math.floor(t)
        end
        if connection and connection:getError() then fail(connection:getError()) end
    end)
    if not ok then fail(err) end
end

function pd.gameWillTerminate() if not finished then finish() end end
