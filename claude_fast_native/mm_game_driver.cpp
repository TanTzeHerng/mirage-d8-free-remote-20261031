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
    double started=0,stable_since=0,last_prog=0,last_poll=0,last_snapshot=-1;
    std::string last_display;
    int next_cmd=1;
    // A durable CPU/RAM/timer forensic snapshot is written while the original
    // Mirage ROM is searching. We DO NOT deserialize C++ callbacks from it:
    // after a hard reset we replay the full legal game and SEARCH AFRESH.
    auto save_native_search=[&](double elapsed,const std::string& display) {
      try {
        fs::path tmp=base/"native_search_state.bin.tmp";
        std::ofstream out(tmp,std::ios::binary|std::ios::trunc);
        if(!out)throw std::runtime_error("Cannot save native search CPU state");
        const char magic[8]={'M','R','G','C','P','U','0','1'};
        out.write(magic,sizeof(magic));
        auto append=[&](const auto& field) {
          out.write(reinterpret_cast<const char*>(&field),sizeof(field));
        };
        append(m.R); append(m.P); append(m.X); append(m.D); append(m.B); append(m.T);
        append(m.I); append(m.N); append(m.DF); append(m.IE); append(m.Q);
        append(m.op); append(m.state); append(m.mode); append(m.pmode);
        append(m.wait_line); append(m.clear_line); append(m.EF);
        append(m.irq); append(m.irq_curstate); append(m.icount);
        append(m.cycles_running); append(m.cycles_stolen);
        append(m.mem); append(m.keys); append(m.keys_pending);
        append(m.m_reset); append(m.kp_mux); append(m.digit_data);
        append(m.digit_idx); append(m.strobe); append(m.digits);
        append(m.lcd_updates); append(m.io_out); append(m.io_in);
        append(m.rand_seed); append(m.gec_calls); append(m.ext_count);
        append(m.basetime); append(m.localtime); append(m.totalcycles);
        append(m.timers); append(m.seqctr); append(m.executing);
        append(m.irq_taken);
        out.close();
        fs::rename(tmp,base/"native_search_state.bin");
        std::ofstream meta(base/"native_search_state.json.tmp");
        meta<<"{\"format\":\"MRGCPU01\",\"contains\":\"registers, full firmware-visible memory, timers, keypad and RNG\","
            <<"\"emulated_search_seconds\":"<<elapsed<<","
            <<"\"cycle_count\":"<<m.totalcycles<<","
            <<"\"display\":\""<<display<<"\","
            <<"\"can_resume_exact_search\":false,"
            <<"\"hard_recovery\":\"preserve search snapshot, replay entire game, start fresh search\"}\n";
        meta.close();
        fs::rename(base/"native_search_state.json.tmp",base/"native_search_state.json");
      }catch(const std::exception& e) {
        std::cerr<<"NATIVE_SNAPSHOT_WARN "<<e.what()<<"\n";
      }
    };
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
        started=now();last_prog=started;last_snapshot=-1;searching=true;
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
        if(last_snapshot<0 || t-last_snapshot>=30.0) {
          save_native_search(t-started,d);
          last_snapshot=t;
        }
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
        h.call([&,move](Machine&){
          emit("OPP_ACK "+move);
          started=now();last_prog=started;last_snapshot=-1;last_display.clear();stable_since=started;searching=true;
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
