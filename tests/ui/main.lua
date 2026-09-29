-- Native UI smoke test: real app navigation and drawing, in-memory media only.
import 'CoreLibs/graphics'
local pd, gfx = playdate, playdate.graphics
pd.datastore.read = function() return {url='http://127.0.0.1:8000', token='ui-fixture'} end
import 'app'

local movie = {id='movie', name='The Grand Budapest Hotel', type='Movie', year=2014,
    duration=5940, resume=1842, played=false, series='',
    overview='A legendary concierge. A trusted lobby boy. An unlikely friendship, and a rather remarkable adventure.'}
local episode = {id='episode', name='The One with a Very Long Title That Should Fit Gracefully',
    type='Episode', season=2, episode=4, year=2025, series='A Series of Small Adventures',
    duration=1470, resume=0, overview='A small adventure becomes a big day out.', played=true}
local folder = {id='series', name='A Series of Small Adventures', type='Series', folder=true,
    series='', duration=0, resume=0, overview='', played=false}
local media = {movie, episode, folder, {id='other', name='An *Unusual* _Title_', type='Movie',
    duration=6300, resume=0, year=2023, series='', overview='', played=false}}
local request, seen, key, crank, failed, page
Net.request = function(method, path, body, callback) request={path=path, callback=callback} end
Net.update = function()
    if not request then return end
    local r=request; request=nil
    if failed then r.callback(nil, 'The bridge is taking a little too long to respond. Try again.'); return end
    if r.path == '/api/status' then r.callback({server='Living room'})
    elseif r.path == '/api/libraries' then r.callback({items={
        {id='movies', name='Movies'}, {id='shows', name='TV Shows'},
        {id='shorts', name='Short Films'}, {id='docs', name='Documentaries'}}})
    elseif r.path == '/api/items/movie' then r.callback(movie)
    elseif r.path == '/api/items/episode' then r.callback(episode)
    elseif r.path:match('view=resume') then r.callback({items={}, total=0})
    elseif r.path:match('parent=movies') then
        page = r.path:match('start=(%d+)')
        local items={}
        for i=1,20 do items[i]=media[(i-1)%#media+1] end
        r.callback({items=items, total=40})
    else error('Unexpected fixture request: '..r.path) end
end
pd.buttonJustPressed = function(button) return key==button end
pd.getCrankChange = function() local value=crank or 0; crank=0; return value end
local browser, player = UI.browser, UI.player
UI.browser = function(screen) seen=screen; browser(screen) end
UI.player = function(...) seen={kind='player'}; player(...) end
Player.play = function(item, position)
    Player.item=item; Player.position=position; Player.state='paused'
    Player.frame=gfx.image.new(400, 240, gfx.kColorWhite)
    gfx.pushContext(Player.frame)
    gfx.setColor(gfx.kColorBlack)
    gfx.fillCircleAtPoint(290, 80, 30)
    gfx.fillTriangle(0, 180, 140, 60, 280, 180)
    gfx.fillTriangle(170, 180, 310, 100, 400, 180)
    gfx.popContext()
end
Player.update = function() end
Player.time = function() return Player.position end
Player.stop = function() Player.state='idle'; Player.frame=nil end
Player.seek = function(position) Player.position=position; Player.state='paused' end
Player.pause = function() Player.state=Player.state=='paused' and 'playing' or 'paused' end

local appUpdate=pd.update
local output = pd.argv[2]
assert(output, 'Pass an absolute screenshot output directory')
local function tick(button, rotation)
    key, crank=button, rotation
    appUpdate()
    key=nil
end
local function capture(name)
    pd.simulator.writeToFile(gfx.getWorkingImage(), output..'/'..name..'.png')
end
local steps = {
    function()
        tick(); tick()
        assert(seen.home and #seen.rows==7)
        capture('01-home')
    end,
    function()
        tick(pd.kButtonA); tick()
        assert(#seen.rows==0)
        capture('02-empty')
        tick(pd.kButtonB); tick()
    end,
    function()
        tick(nil, 30)
        assert(seen.selected==3, 'Crank should select Movies')
        tick(pd.kButtonA); tick()
        assert(seen.title=='Movies' and #seen.rows==20)
        capture('03-library')
    end,
    function()
        tick(pd.kButtonRight); tick()
        assert(page=='20' and seen.start==20, 'Next page')
        capture('04-page-two')
        tick(pd.kButtonLeft); tick()
        assert(page=='0' and seen.start==0, 'Previous page')
        tick(nil, 285)
        assert(seen.selected==20, 'Crank should reach last row')
        capture('05-library-bottom')
        tick(nil, -285)
    end,
    function()
        tick(pd.kButtonA); tick()
        assert(seen.kind=='detail' and #seen.rows==2)
        capture('06-details')
        seen.poster=gfx.image.new(96, 144, gfx.kColorBlack)
        gfx.pushContext(seen.poster)
        gfx.setColor(gfx.kColorWhite)
        gfx.drawRect(5, 5, 86, 134)
        gfx.setImageDrawMode(gfx.kDrawModeFillWhite)
        gfx.drawTextInRect('SAMPLE\nPOSTER', 8, 51, 80, 48, nil, nil, kTextAlignment.center)
        gfx.setImageDrawMode(gfx.kDrawModeCopy)
        gfx.popContext()
        browser(seen)
        capture('15-poster-details')
    end,
    function()
        tick(pd.kButtonA); tick()
        assert(seen.kind=='player' and Player.position==movie.resume, 'Resume position')
        capture('07-paused')
        tick(pd.kButtonRight)
        capture('08-seek')
        tick(pd.kButtonA)
        assert(Player.position==movie.resume+10, 'Seek should commit with A')
        tick(pd.kButtonA)
        assert(Player.state=='playing')
        gfx.clear(gfx.kColorWhite)
        player(Player, nil, pd.getCurrentTimeMilliseconds()+3000, pd.getCurrentTimeMilliseconds())
        capture('09-playback-overlay')
    end,
    function()
        tick(pd.kButtonB); tick()
        assert(seen.kind=='detail')
        tick(pd.kButtonB); tick()
        assert(seen.title=='Movies' and seen.selected==1, 'Back preserves list selection')
        tick(pd.kButtonDown); tick(pd.kButtonA); tick()
        assert(seen.kind=='detail' and #seen.rows==1)
        capture('10-long-title')
        tick(pd.kButtonB); tick(); tick(pd.kButtonB); tick()
        assert(seen.home)
        tick(nil, 300); tick(pd.kButtonA); tick()
        assert(seen.title=='Connection')
        capture('11-connection')
    end,
    function()
        tick(pd.kButtonB); tick()
        assert(seen.home)
        failed=true
        tick(nil, -300); tick(pd.kButtonA); tick()
        assert(seen.kind=='error')
        capture('12-error')
        failed=false
        tick(pd.kButtonA); tick()
        assert(seen.kind=='list' and #seen.rows==0, 'Retry recovers')
        browser({kind='loading', title='Movies', message='Loading your library...'})
        capture('13-loading')
        browser({kind='list', title='Search: Moon', subtitle='0 titles', query='search=Moon', start=0,
            total=0, selected=1, rows={}})
        capture('14-empty-search')
    end,
}
local frame, exitAt=0, nil
local function finish(ok, message)
    local result=assert(pd.file.open('result.json', pd.file.kFileWrite))
    result:write(json.encode({ok=ok, message=message, steps=frame})); result:close()
    print(message)
    pd.gameWillTerminate()
    pd.gameWillTerminate=nil
    -- Let the Simulator finish presenting the final frame before closing.
    exitAt=pd.getCurrentTimeMilliseconds()+1000
end
function pd.update()
    if exitAt then
        if pd.getCurrentTimeMilliseconds() >= exitAt then pd.simulator.exit() end
        return
    end
    frame=frame+1
    local ok, err=pcall(steps[frame])
    if not ok then finish(false, 'UI SMOKE FAILED: '..tostring(err)); return end
    if frame==#steps then
        finish(true, 'UI SMOKE PASSED: navigation, paging, crank, details, resume, seek, back, retry; 15 screenshots')
    end
end
