-- Native, one-bit UI. All coordinates are for Playdate's 400 x 240 display.
UI = {}
local pd, gfx = playdate, playdate.graphics
local body = gfx.getSystemFont()
local bold = gfx.getSystemFont('bold')
local small = assert(gfx.font.new('fonts/Roobert-10-Bold'))
local heading = assert(gfx.font.new('fonts/Roobert-20-Medium'))

function UI.time(seconds)
    seconds = math.max(0, math.floor(seconds or 0))
    local hours = math.floor(seconds/3600)
    if hours > 0 then return string.format('%d:%02d:%02d', hours, math.floor(seconds/60)%60, seconds%60) end
    return string.format('%d:%02d', math.floor(seconds/60), seconds%60)
end

local function text(value, x, y, width, height, font, white, alignment)
    gfx.setImageDrawMode(white and gfx.kDrawModeFillWhite or gfx.kDrawModeCopy)
    -- A single font keeps punctuation in media titles from becoming markup.
    gfx.drawTextInRect(tostring(value or ''), x, y, width, height, 0, '...',
        alignment or kTextAlignment.left, font or body)
    gfx.setImageDrawMode(gfx.kDrawModeCopy)
end

local function icon(kind, x, y, white)
    gfx.setColor(white and gfx.kColorWhite or gfx.kColorBlack)
    gfx.setLineWidth(2)
    if kind == 'search' then
        gfx.drawCircleAtPoint(x+7, y+7, 5)
        gfx.drawLine(x+11, y+11, x+16, y+16)
    elseif kind == 'resume' then
        gfx.drawCircleAtPoint(x+9, y+9, 8)
        gfx.drawLine(x+9, y+4, x+9, y+9)
        gfx.drawLine(x+9, y+9, x+13, y+11)
    elseif kind == 'settings' then
        gfx.drawLine(x+2, y+4, x+16, y+4)
        gfx.drawLine(x+2, y+13, x+16, y+13)
        gfx.fillRect(x+5, y+1, 3, 7)
        gfx.fillRect(x+11, y+10, 3, 7)
    elseif kind == 'pause' then
        gfx.fillRect(x+3, y+2, 4, 14)
        gfx.fillRect(x+11, y+2, 4, 14)
    elseif kind == 'play' then
        gfx.fillTriangle(x+4, y+1, x+4, y+17, x+16, y+9)
    elseif kind == 'check' then
        gfx.drawLine(x+2, y+9, x+7, y+14)
        gfx.drawLine(x+7, y+14, x+16, y+3)
    elseif kind == 'error' then
        gfx.drawCircleAtPoint(x+9, y+9, 8)
        gfx.fillRect(x+8, y+4, 2, 6)
        gfx.fillRect(x+8, y+13, 2, 2)
    else
        gfx.drawRoundRect(x+1, y+3, 16, 12, 2)
        gfx.drawLine(x+5, y, x+9, y+3)
        gfx.drawLine(x+13, y, x+9, y+3)
        gfx.drawLine(x+5, y+18, x+13, y+18)
    end
    gfx.setLineWidth(1)
end

local function button(letter, label, x, white)
    gfx.setColor(white and gfx.kColorWhite or gfx.kColorBlack)
    gfx.fillCircleAtPoint(x+8, 227, 8)
    text(letter, x, 220, 15, 14, small, not white, kTextAlignment.center)
    text(label, x+22, 221, 90, 14, small, white)
end

local function footer(action, back, hint, white)
    gfx.setColor(gfx.kColorBlack)
    if white then gfx.fillRect(0, 215, 400, 25)
    else gfx.drawLine(12, 213, 388, 213) end
    if action then button('A', action, 12, white) end
    if back then button('B', back, action and 116 or 12, white) end
    text(hint, 218, 221, 170, 14, small, white, kTextAlignment.right)
end

local function progress(position, duration, x, y, width, white)
    gfx.setColor(white and gfx.kColorWhite or gfx.kColorBlack)
    gfx.drawRoundRect(x, y, width, 6, 2)
    local fraction = duration > 0 and math.max(0, math.min(1, position/duration)) or 0
    local filled = math.floor((width-4)*fraction)
    if filled > 0 then gfx.fillRect(x+2, y+2, filled, 2) end
end

local function header(title, subtitle, count)
    gfx.clear(gfx.kColorWhite)
    local info = count or subtitle or ''
    local infoWidth = math.min(140, small:getTextWidth(info))
    text(title, 12, 7, 376-(infoWidth > 0 and infoWidth+12 or 0), 32, heading)
    text(info, 388-infoWidth, 18, infoWidth, 14, small, false, kTextAlignment.right)
    gfx.setColor(gfx.kColorBlack)
    gfx.drawLine(12, 44, 388, 44)
end

local function metadata(item, withSeries)
    local parts = {}
    if withSeries and item.series and item.series ~= '' then parts[#parts+1] = item.series end
    if item.year then parts[#parts+1] = tostring(item.year) end
    if item.duration and item.duration > 0 then parts[#parts+1] = UI.time(item.duration) end
    if item.played then parts[#parts+1] = 'Watched'
    elseif item.resume and item.resume > 2 then parts[#parts+1] = 'Resume '..UI.time(item.resume) end
    if #parts == 0 then return item.folder and 'Browse titles' or 'Video' end
    return table.concat(parts, '  /  ')
end

local function rows(screen, top, rowHeight, visible, left)
    left = left or 12
    local count = #screen.rows
    local first = math.max(1, math.min(screen.selected-math.floor(visible/2), count-visible+1))
    local scroll = count > visible
    local width = (scroll and 383 or 388)-left
    for i=first, math.min(count, first+visible-1) do
        local row, y = screen.rows[i], top+(i-first)*rowHeight
        local selected = i == screen.selected
        local centerY = y+(rowHeight-3)//2
        gfx.setColor(gfx.kColorBlack)
        if selected then gfx.fillRoundRect(left, y, width, rowHeight-2, 5) end
        local note = row.note or (row.item and metadata(row.item, not screen.folder))
        local symbol = row.icon or (row.item and (row.item.folder and 'library' or 'play')) or 'settings'
        icon(symbol, left+9, centerY-8, selected)
        text(row.label, left+36, note and y+2 or centerY-8, width-72, 22, selected and bold or body, selected)
        if note then text(note, left+36, y+20, width-74, 14, small, selected) end
        gfx.setColor(selected and gfx.kColorWhite or gfx.kColorBlack)
        gfx.drawLine(left+width-15, centerY-3, left+width-12, centerY)
        gfx.drawLine(left+width-12, centerY, left+width-15, centerY+3)
    end
    if scroll then
        local height = rowHeight*visible-4
        gfx.setColor(gfx.kColorBlack)
        gfx.drawLine(389, top+2, 389, top+height)
        local thumb = math.max(10, math.floor(height*visible/count))
        local y = top+math.floor((height-thumb)*(first-1)/(count-visible))
        gfx.fillRoundRect(387, y, 5, thumb, 2)
    end
end

local function waiting(y, now)
    gfx.setColor(gfx.kColorBlack)
    local active = math.floor(now/250)%3
    for i=0,2 do
        if i == active then gfx.fillCircleAtPoint(188+i*12, y, 3)
        else gfx.drawCircleAtPoint(188+i*12, y, 3) end
    end
end

local function poster(image, x, y)
    if image then image:draw(x, y); return end
    gfx.setColor(gfx.kColorBlack)
    gfx.drawRoundRect(x, y, 96, 144, 4)
    gfx.drawRect(x+26, y+52, 44, 36)
    gfx.drawCircleAtPoint(x+38, y+62, 4)
    gfx.drawLine(x+28, y+85, x+43, y+70)
    gfx.drawLine(x+43, y+70, x+52, y+79)
    gfx.drawLine(x+52, y+79, x+61, y+68)
    gfx.drawLine(x+61, y+68, x+68, y+76)
end

function UI.browser(screen)
    if screen.kind == 'list' then
        if screen.home then
            gfx.clear(gfx.kColorWhite)
            gfx.setColor(gfx.kColorBlack)
            gfx.fillRoundRect(12, 8, 376, 38, 7)
            text(screen.subtitle, 24, 11, 352, 32, heading, true)
            rows(screen, 55, 30, 5)
            footer('Open', nil, 'D-pad / crank: scroll')
        else
            local count = screen.query and screen.total > 0 and
                string.format('%d / %d', (screen.start or 0)+screen.selected, screen.total) or nil
            header(screen.title, screen.subtitle, count)
            local hasPoster = screen.folder ~= nil or screen.poster ~= nil
            local left = hasPoster and 120 or 12
            local width = 388-left
            if hasPoster then poster(screen.poster, 12, 52) end
            if #screen.rows == 0 then
                icon('search', left+(width-18)//2, 87)
                local search = screen.query and screen.query:match('^search=')
                local resume = screen.query == 'view=resume'
                text(search and 'No matches' or resume and 'Nothing to resume' or 'No videos',
                    left+8, 119, width-16, 26, bold, false, kTextAlignment.center)
                text(search and 'Try another title.' or resume and 'Start a video to resume it here.' or 'Try another library.',
                    left+8, 151, width-16, 28, small, false, kTextAlignment.center)
            else rows(screen, 50, 38, 4, left) end
            local hint = screen.query and screen.total > 20 and
                string.format('< %d/%d > pages', screen.start//20+1, math.ceil(screen.total/20)) or 'D-pad / crank: scroll'
            footer(#screen.rows > 0 and 'Open' or nil, 'Back', #screen.rows > 0 and hint or nil)
        end
    elseif screen.kind == 'detail' then
        gfx.clear(gfx.kColorWhite)
        local item = screen.item
        local left = 120
        poster(screen.poster, 12, 8)
        local _, titleHeight = gfx.getTextSizeForMaxWidth(screen.title, 388-left, 0, heading)
        titleHeight = math.min(64, titleHeight)
        text(screen.title, left, 7, 388-left, titleHeight, heading)
        local metaY = 10+titleHeight
        text(metadata(item), left+1, metaY, 386-left, 14, small)
        gfx.setColor(gfx.kColorBlack)
        gfx.drawLine(left, metaY+20, 388, metaY+20)
        local description = item.overview ~= '' and item.overview or 'No description available.'
        if item.type == 'Episode' then
            description = string.format('%s / S%02d E%02d\n%s', item.series or '', item.season or 0, item.episode or 0, description)
        end
        local actionsTop = 209-#screen.rows*32
        local descriptionBottom = actionsTop-(item.resume > 2 and 17 or 8)
        text(description, left+1, metaY+28, 386-left, descriptionBottom-metaY-28, body)
        if item.resume > 2 then progress(item.resume, item.duration, left+1, actionsTop-11, 386-left) end
        rows(screen, actionsTop, 32, 2, left)
        footer('Play', 'Back', nil)
    elseif screen.kind == 'error' then
        header('Connection problem')
        icon('error', 14, 60)
        text(screen.error, 44, 57, 342, 130, body)
        footer('Retry', 'Back', 'Menu: Connection')
    else
        header(screen.title or 'Jellyfin', 'LOADING')
        waiting(103, pd.getCurrentTimeMilliseconds())
        text(screen.message or 'Loading...', 28, 129, 344, 50, body, false, kTextAlignment.center)
        footer(nil, 'Back', nil)
    end
end

function UI.player(player, seekTarget, overlayUntil, now)
    local state, item = player.state, player.item
    local position, duration = seekTarget or player.time(), item and item.duration or 0
    -- The decoder owns its image; UI drawing never touches the held frame.
    if player.frame then player.frame:draw(0, 0) else gfx.clear(gfx.kColorBlack) end
    if state == 'playing' and now >= (overlayUntil or 0) and not seekTarget and not player.progressWarning then return end
    gfx.setColor(gfx.kColorBlack)
    gfx.fillRect(0, 0, 400, 34)
    text(item and item.name or 'Jellyfin', 12, 7, 260, 24, bold, true)
    text('NOW SHOWING', 278, 11, 110, 14, small, true, kTextAlignment.right)

    gfx.fillRect(0, 183, 400, 57)
    local labels = {loading='Starting', buffering='Buffering', paused='Paused',
        pausing='Pausing', seeking='Seeking', ended='Finished', error='Playback interrupted', playing='Playing'}
    text(seekTarget and 'Seek' or labels[state] or 'Loading', 12, 187, 172, 14, small, true)
    text(UI.time(position)..' / '..UI.time(duration), 188, 187, 200, 14, small, true, kTextAlignment.right)
    progress(position, duration, 12, 205, 376, true)
    local message = state == 'error' and player.error or
        player.progressWarning and 'Your watch progress could not be saved.'
    if message then
        gfx.setColor(gfx.kColorBlack)
        gfx.fillRect(0, 143, 400, 40)
        text(message, 12, 148, 376, 30, small, true)
    end
    local action = seekTarget and 'Seek' or state == 'playing' and 'Pause' or state == 'paused' and 'Resume' or state == 'ended' and 'Replay' or
        state == 'error' and 'Retry' or state == 'buffering' and 'Pause' or nil
    footer(action, 'Back', (state == 'playing' or state == 'paused' or state == 'buffering') and 'Crank / L-R: seek' or nil, true)
end
