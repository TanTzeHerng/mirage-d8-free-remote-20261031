#pragma once
#include "mm_core.h"
#include <deque>
#include <string>
#include <map>
#include <cmath>

namespace mm {

// key name -> (port, mask).  IN.0: CL A/1 ENT B/2 STA C/3 LEV D/4 ; IN.1: LIST E/5 BL/9 F/6 WH/0 G/7 REV H/8
struct KeyDef { int port; uint8_t mask; };
inline bool key_lookup(const std::string &k, KeyDef &d) {
    static const std::map<std::string, KeyDef> m = {
        {"CL", {0, 0x01}}, {"A", {0, 0x02}}, {"1", {0, 0x02}}, {"ENT", {0, 0x04}}, {"B", {0, 0x08}}, {"2", {0, 0x08}},
        {"STA", {0, 0x10}}, {"C", {0, 0x20}}, {"3", {0, 0x20}}, {"LEV", {0, 0x40}}, {"D", {0, 0x80}}, {"4", {0, 0x80}},
        {"LIST", {1, 0x01}}, {"E", {1, 0x02}}, {"5", {1, 0x02}}, {"BL", {1, 0x04}}, {"9", {1, 0x04}}, {"F", {1, 0x08}}, {"6", {1, 0x08}},
        {"WH", {1, 0x10}}, {"0", {1, 0x10}}, {"G", {1, 0x20}}, {"7", {1, 0x20}}, {"REV", {1, 0x40}}, {"H", {1, 0x80}}, {"8", {1, 0x80}},
    };
    auto it = m.find(k);
    if (it == m.end()) return false;
    d = it->second; return true;
}

// 7-segment decode (MAME digit outputs: bit0=a .. bit6=g, bit7 = dp); same table as explore.lua
inline char seg_char(uint8_t v) {
    switch (v & 0x7f) {
    case 0x00: return ' ';
    case 0x3f: return '0'; case 0x06: return '1'; case 0x5b: return '2'; case 0x4f: return '3';
    case 0x66: return '4'; case 0x6d: return '5'; case 0x7d: return '6'; case 0x07: return '7';
    case 0x7f: return '8'; case 0x6f: return '9';
    case 0x77: return 'A'; case 0x7c: return 'b'; case 0x39: return 'C'; case 0x5e: return 'd';
    case 0x79: return 'E'; case 0x71: return 'F'; case 0x3d: return 'G'; case 0x76: return 'H';
    case 0x40: return '-'; case 0x08: return '_'; case 0x38: return 'L'; case 0x50: return 'r';
    case 0x37: return 'N'; case 0x54: return 'n'; case 0x73: return 'P'; case 0x5c: return 'o';
    case 0x1c: return 'u'; case 0x3e: return 'U'; case 0x78: return 't'; case 0x6e: return 'y';
    case 0x31: return 'T'; case 0x33: return 'M'; case 0x27: return 'K';
    default: return '?';
    }
}

// Scripted input, executed from the T_HARNESS timer exactly like a MAME Lua
// coroutine using emu.wait(): every action happens at an exact emulated time.
struct Step {
    enum Kind { WAIT, SET, CLEAR, CALL } kind;
    double secs;           // for WAIT
    int port; uint8_t mask;
    std::function<void(Machine&)> fn;
};

struct Harness {
    Machine &m;
    std::deque<Step> steps;
    bool idle = true;
    explicit Harness(Machine &mm) : m(mm) {
        m.harness_cb = [this](Machine &) { this->resume(); };
    }
    static atto_t from_double(double t) {
        int64_t secs = (int64_t)floor(t);
        t -= (double)secs;
        int64_t attos = (int64_t)(t * (double)ATTO_PER_SEC);
        return (atto_t)secs * ATTO_PER_SEC + attos;
    }
    void set_port(int port, uint8_t mask, int v) {
        // ioport_field::set_value(): only the pending digital value changes; it is latched
        // into the live port value at the next frame update (60 Hz screenless frame timer)
        if (v) m.keys_pending[port] |= mask; else m.keys_pending[port] &= (uint8_t)~mask;
    }
    void resume() {
        while (!steps.empty()) {
            Step s = steps.front(); steps.pop_front();
            if (s.kind == Step::WAIT) {
                m.timer_adjust(T_HARNESS, from_double(s.secs), 0);
                idle = false;
                return;
            } else if (s.kind == Step::SET) set_port(s.port, s.mask, 1);
            else if (s.kind == Step::CLEAR) set_port(s.port, s.mask, 0);
            else if (s.kind == Step::CALL) s.fn(m);
        }
        idle = true;
    }
    // press(tag, mask, secs) of explore.lua / send_input of the chessengine plugin
    void key(const std::string &k, double seconds = 0.5) {
        KeyDef d; if (!key_lookup(k, d)) { fprintf(stderr, "bad key %s\n", k.c_str()); exit(1); }
        steps.push_back({Step::SET, 0, d.port, d.mask, nullptr});
        steps.push_back({Step::WAIT, seconds * 2 / 3, 0, 0, nullptr});
        steps.push_back({Step::CLEAR, 0, d.port, d.mask, nullptr});
        steps.push_back({Step::WAIT, seconds * 1 / 3, 0, 0, nullptr});
    }
    void wait(double s) { steps.push_back({Step::WAIT, s, 0, 0, nullptr}); }
    void call(std::function<void(Machine&)> f) { steps.push_back({Step::CALL, 0, 0, 0, f}); }
    bool started = false;
    void start_on_first_frame() {
        idle = false;
        m.frame_cb = [this](Machine &) {
            if (!started) { started = true; resume(); }
        };
    }
};

inline std::string display_str(const Machine &m) {
    std::string s;
    for (int i = 3; i >= 0; i--) s += seg_char(m.digits[i]);   // digit3 is leftmost
    return s;
}
inline std::string display_str_dots(const Machine &m) {          // same text as explore.lua show()
    std::string s;
    for (int i = 3; i >= 0; i--) { s += seg_char(m.digits[i]); if (m.digits[i] & 0x80) s += '.'; }
    return s;
}

} // namespace mm