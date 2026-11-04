-- Genuine MAME 0.238 mmirage controller. Does not touch Mephisto I.
local BASE = [[@@BASE@@]]
local outfile=assert(io.open(BASE.."worker.out","w"))
local log=function(x) outfile:write(x.."\n");outfile:flush() end
local file=assert(io.open(BASE.."job.txt","r"))
local words={}
for line in file:lines() do
 local w={}
 for a in line:gmatch("%S+") do w[#w+1]=a end
 if #w>0 then words[#words+1]=w end
end
file:close()
local seg={ [0x00]=' ',[0x3f]='0',[0x06]='1',[0x5b]='2',[0x4f]='3',[0x66]='4',
 [0x6d]='5',[0x7d]='6',[0x07]='7',[0x7f]='8',[0x6f]='9',[0x77]='A',
 [0x7c]='b',[0x39]='C',[0x5e]='d',[0x79]='E',[0x71]='F',
 [0x3d]='G',[0x76]='H',[0x40]='-',[0x08]='_',[0x38]='L',
 [0x50]='r',[0x37]='N',[0x54]='n',[0x73]='P' }
local K={
 CL={":IN.0",0x01}, A={":IN.0",0x02}, ["1"]={":IN.0",0x02},
 ENT={":IN.0",0x04}, B={":IN.0",0x08}, ["2"]={":IN.0",0x08},
 STA={":IN.0",0x10}, C={":IN.0",0x20}, ["3"]={":IN.0",0x20},
 LEV={":IN.0",0x40}, D={":IN.0",0x80}, ["4"]={":IN.0",0x80},
 LIST={":IN.1",0x01}, E={":IN.1",0x02}, ["5"]={":IN.1",0x02},
 BL={":IN.1",0x04}, ["9"]={":IN.1",0x04}, F={":IN.1",0x08},
 ["6"]={":IN.1",0x08}, WH={":IN.1",0x10}, ["0"]={":IN.1",0x10},
 G={":IN.1",0x20}, ["7"]={":IN.1",0x20}, REV={":IN.1",0x40},
 H={":IN.1",0x80}, ["8"]={":IN.1",0x80} }
local function press(x)
 local k=assert(K[x],"bad key "..tostring(x))
 manager.machine.ioport.ports[k[1]]:field(k[2]):set_value(1)
 emu.wait(0.5 * 2 / 3)
 manager.machine.ioport.ports[k[1]]:field(k[2]):set_value(0)
 emu.wait(0.5 * 1 / 3)
end
local function now()
 local t=manager.machine.time
 return t.seconds+(t.attoseconds/1e18)
end
local function disp()
 local s=""
 local o=manager.machine.output
 for i=3,0,-1 do s=s..(seg[o:get_value("digit"..i) & 0x7f] or '?') end
 return s
end
local function move_display(s)
 return s:match("^[A-Hbd][1-8][A-Hbd][1-8]$")~=nil
end
local function main()
 emu.wait(1)
 press("CL");press("LEV");press("D");press("8");press("ENT")
 for c in ("9999"):gmatch(".") do press(c) end
 press("ENT")
 log("LEVEL D8 9999 DISPLAY "..disp())
 for _,m in ipairs({"e2e4","e7e5"}) do
  for c in m:upper():gmatch(".") do press(c) end
  press("ENT")
  emu.wait(1)
  log("REPLAY_ENTER "..m.." DISPLAY "..disp().." TIME "..string.format("%.2f",now()))
 end
 log("REPLAY_PROBE_FINISHED")
 outfile:close()
 manager.machine:exit()
end
local co
emu.register_periodic(function()
 if co==nil then
  co=coroutine.create(main)
  local ok,err=coroutine.resume(co)
  if not ok then log("LUA_ERROR "..tostring(err));outfile:close();manager.machine:exit() end
 end
end)

