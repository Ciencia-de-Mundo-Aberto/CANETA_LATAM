-- Wide "simple" Markdown tables have no column widths: Word and LaTeX then size each column to its
-- longest line and the table runs past the margin. Give such tables relative widths (proportional to
-- the longest text of each column, with a floor) that add up to the text width; text wraps inside cells.

local FLOOR = 0.10    -- no column narrower than 10% of the text width
local MIN_CHARS = 70  -- tables whose longest row is shorter than this keep automatic widths

local function text_len(blocks)
  local s = pandoc.utils.stringify(blocks)
  return utf8.len(s) or #s
end

function Table(tbl)
  local n = #tbl.colspecs
  if n == 0 then return nil end
  for _, spec in ipairs(tbl.colspecs) do
    if spec[2] and spec[2] ~= 0 and spec[2] ~= pandoc.ColWidthDefault then return nil end  -- author-set
  end
  local longest = {}
  for i = 1, n do longest[i] = 1 end
  local function scan(rows)
    for _, row in ipairs(rows) do
      for i, cell in ipairs(row.cells) do
        if i <= n then longest[i] = math.max(longest[i], text_len(cell.contents)) end
      end
    end
  end
  scan(tbl.head.rows)
  for _, body in ipairs(tbl.bodies) do scan(body.body) end
  local total = 0
  for i = 1, n do total = total + longest[i] end
  if total < MIN_CHARS then return nil end
  local widths, sum = {}, 0
  for i = 1, n do
    widths[i] = math.max(longest[i] / total, FLOOR)
    sum = sum + widths[i]
  end
  for i = 1, n do
    tbl.colspecs[i] = {tbl.colspecs[i][1], widths[i] / sum * 0.98}
  end
  return tbl
end
