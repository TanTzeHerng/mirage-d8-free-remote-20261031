// Claude's native Mirage CPU core, adapted to drive uninterrupted full-history chess games.
// Uses the actual Mirage G79 firmware; accepts the same file protocol as the MAME Lua driver.
#include "mm_harness.h"
#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <regex>
#include <stdexcept>
#include <string>
#include <vector>
using namespace mm;
namespace fs=std::filesystem;
static std::string trim(std::string s) {
  auto a=s.find_first_not_of(" \t\r\n"), b=s.find_last_not_of(" \t\r\n");
  return a==std::string::npos?"":s.substr(a,b-a+1);
}
static std::vector<std::string> lines(const fs::path& path) {
  std::ifstream in(path); if(!in)throw std::runtime_error("No "+path.string());
  std::vector<std::string> out;std::string s;
  while(std::getline(in,s)){s=trim(s);if(!s.empty())out.push_back(s);}
  return out;
}
static bool is_move(const std::string& s) {
  if(s.size()!=4)return false;
  auto file=[](char c){return (c>='A'&&c<='H')||c=='b'||c=='d';};
  auto rank=[](char c){return c>='1'&&c<='8';};
  return file(s[0])&&rank(s[1])&&file(s[2])&&rank(s[3]);
}
static void queue_move(Harness& h,const std::string& m,bool calculate) {
  if(m.size()!=4)throw std::runtime_error("Invalid UCI "+m);
  for(char c:m){
    if(c>='a'&&c<='h')c=char(c-'a'+'A');
    h.key(std::string(1,c));
  }
  h.key(calculate?"STA":"ENT");
}
int main(int argc,char** argv) {
  try {
    if(argc!=3)throw std::runtime_error("Usage: mm_game_driver WORKER_DIRECTORY G79_ROM");
    fs::path base=fs::absolute(argv[1]);
    std::ifstream romfile(argv[2],std::ios::binary);
    if(!romfile)throw std::runtime_error("Cannot open genuine G79 ROM");
    static uint8_t rom[0x8000];romfile.read((char*)rom,sizeof(rom));
    if(romfile.gcount()!=sizeof(rom))throw std::runtime_error("G79 must be exactly 32768 bytes");
    static Machine m;m.load_rom(rom);m.power_on();
    std::ofstream log(base/"worker.out",std::ios::app);
    if(!log)throw std::runtime_error("Cannot open worker log");
    auto emit=[&](const std::string& s){log<<s<<"\n";log.flush();std::cout<<s<<"\n";std::cout.flush();};
    auto now=[&](){return (double)(m.basetime/1000000)/1e12;};
    std::vector<std::string> book=lines(base/"book_moves.txt");
    std::vector<std::string> mode=lines(base/"start_mode.txt");
    if(mode.size()!=1 || (mode[0]!="MIRAGE"&&mode[0]!="MAIA"))throw std::runtime_error("Invalid start_mode");
    bool ready=false,searching=false;
    double started=0,stable_since=0,last_prog=0,last_poll=0;
    std::string last_display;
    int next_cmd=1;
    Harness h(m);
    h.wait(1);h.key("CL");h.key("LEV");h.key("D");h.key("8");h.key("ENT");
    for(char c:std::string("9999"))h.key(std::string(1,c));
    h.key("ENT");
    h.call([&](Machine&){emit("LEVEL D8 MMSS 9999 NATIVE_CLAUDE");});
    for(size_t i=0;i<book.size();++i){
      queue_move(h,book[i],false);
      if((i+1)%5==0)h.call([&,i](Machine&){emit("BOOK_MOVE_COUNT "+std::to_string(i+1));});
    }
    h.call([&](Machine&){
      emit("BOOK_DONE "+std::to_string(book.size())+" DISPLAY "+display_str(m));
      ready=true;
    });
    if(mode[0]=="MIRAGE"){
      h.key("STA");
      h.call([&](Machine&){
        started=now();last_prog=started;searching=true;
        emit("SEARCH_START "+std::to_string(started));
      });
    }else{
      h.call([&](Machine&){emit("READY_FOR_MAIA");});
    }
    h.start_on_first_frame();
    for(;;) {
      m.timeslice();
      double t=now();
      if(!ready||!h.idle)continue;
      if(searching) {
        std::string d=display_str(m);
        if(is_move(d)) {
          if(d!=last_display){last_display=d;stable_since=t;}
          else if(t-stable_since>=2.1){
            for(char& c:d)c=(char)std::tolower((unsigned char)c);
            emit("MOVE "+d+" EMU "+std::to_string(t-started));
            searching=false;last_display.clear();emit("READY_FOR_MAIA");
          }
        }else{last_display.clear();stable_since=t;}
        if(t-last_prog>=60){last_prog=t;emit("PROGRESS "+std::to_string(t-started)+" DISPLAY "+d);}
      } else if(t-last_poll>=0.25) {
        last_poll=t;
        fs::path cmdpath=base/("cmd_"+std::to_string(next_cmd)+".txt");
        if(!fs::exists(cmdpath))continue;
        auto list=lines(cmdpath);if(list.empty())throw std::runtime_error("Empty command");
        std::string cmd=list[0];emit("COMMAND "+std::to_string(next_cmd)+" "+cmd);
        ++next_cmd;
        if(cmd=="QUIT"){emit("QUIT_ACK");return 0;}
        if(cmd.rfind("MOVE ",0)!=0 || cmd.size()!=9)throw std::runtime_error("Invalid command "+cmd);
        std::string move=cmd.substr(5);
        queue_move(h,move,true);
        h.call([&](Machine&){
          emit("OPP_ACK "+move);
          started=now();last_prog=started;last_display.clear();stable_since=started;searching=true;
          emit("SEARCH_START "+std::to_string(started));
        });
        h.resume();
      }
    }
  }catch(const std::exception& e){
    std::cerr<<"NATIVE_MIRAGE_ERROR "<<e.what()<<"\n";
    return 2;
  }
}
