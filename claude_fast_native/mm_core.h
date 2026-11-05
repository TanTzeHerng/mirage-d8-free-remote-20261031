// Mephisto Mirage (Hegener + Glaser, 1984) -- cycle-exact re-implementation of the
// MAME 0.238 "mmirage" driver (mephisto_mm1.cpp) + CDP1806 core (cosmac.cpp, CDP1804
// extended opcodes as implemented in 0.238) + the parts of MAME's scheduler that decide
// where CPU timeslices end.  Derived from the verified Mephisto I native build.
#pragma once
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <vector>
#include <string>
#include <functional>

typedef __int128 atto_t;   // absolute emulated time in attoseconds

namespace mm {

static constexpr int64_t ATTO_PER_SEC = 1000000000000000000LL;
static constexpr uint32_t CPU_CLOCK = 8000000;                    // 8 MHz XTAL
static constexpr int64_t APS = ATTO_PER_SEC / CPU_CLOCK;           // 125000000000 attoseconds per clock
static constexpr int DIVSHIFT = 6;
static constexpr uint32_t DIVISOR = (uint32_t)(APS >> DIVSHIFT);   // 1953125000
static_assert(APS == 125000000000LL, "aps");
static_assert((APS >> DIVSHIFT) < (1LL << 31) && (APS >> (DIVSHIFT - 1)) >= (1LL << 31), "divshift");

// attotime::from_ticks(8 * 32 * 0x71, 8_MHz_XTAL)
static constexpr int64_t IRQ_PERIOD = (int64_t)(8 * 32 * 0x71) * APS;

enum TimerId { T_IRQ = 0, T_FRAME, T_SOUND, T_PWM, T_HARNESS, T_COUNT };

struct Timer {
    atto_t expire;      // NEVER if disabled
    atto_t period;      // 0 = one shot
    bool enabled;
    uint64_t seq;       // insertion order (for equal expire: FIFO like MAME's list)
};

static constexpr atto_t NEVER = ((atto_t)1) << 120;

struct Machine {
    // ---------------- CPU state (cosmac_device) ----------------
    uint16_t R[16];
    uint8_t P, X, D, B, T, I, N, DF, IE, Q;
    uint16_t op;
    uint8_t state;     // cosmac_state
    uint8_t mode, pmode;
    uint8_t wait_line, clear_line;
    uint8_t EF[4];
    int irq;           // m_irq (CLEAR=0 / ASSERT=1)
    int irq_curstate;  // device_input m_curstate: 0 clear, 2 HOLD
    int32_t icount;
    int32_t cycles_running;
    int32_t cycles_stolen;

    // ---------------- memory ----------------
    uint8_t rom[0x8000];
    alignas(64) uint8_t mem[0x10000];   // flat read image: ROM 0000-7FFF, RAM D000-DFFF + F000-FFFF (mirror), rest 0
    uint8_t *ram = mem + 0xd000;

    // ---------------- driver ----------------
    bool m_reset;          // mm1_state::m_reset
    uint8_t kp_mux;        // mm1_state::m_kp_mux
    uint8_t keys[2];       // IN.0 .. IN.1 live values (active high bits)
    uint8_t keys_pending[2]; // ioport_field::m_digital_value (applied at next frame update)
    std::function<void(Machine&)> frame_cb;     // Lua periodic callback (runs before ioport frame update)
    uint8_t digit_data[4], digit_idx, strobe, digits[4];
    uint64_t lcd_updates;
    uint64_t io_out[8], io_in[8];
    uint32_t rand_seed;    // running_machine::m_rand_seed
    uint64_t gec_calls;
    uint64_t ext_count[256];

    // ---------------- scheduler ----------------
    atto_t basetime;
    atto_t localtime;      // CPU local time
    uint64_t totalcycles;
    Timer timers[T_COUNT];
    uint64_t seqctr;
    bool executing;
    std::function<void(Machine&)> harness_cb;   // called when T_HARNESS fires

    // statistics
    uint64_t irq_taken;

    void load_rom(const uint8_t *data) { memcpy(rom, data, sizeof(rom)); memset(mem, 0, sizeof(mem)); memcpy(mem, rom, sizeof(rom)); }

    // running_machine::rand()
    uint32_t rnd() {
        rand_seed = 1664525u * rand_seed + 1013904223u;
        return (rand_seed >> 16) | (rand_seed << 16);
    }

    // power-on (MAME hard reset at time 0)
    void power_on(uint32_t seed = 0x9d14abd7) {
        memset(mem, 0, sizeof(mem)); memcpy(mem, rom, sizeof(rom));
        // cosmac constructor defaults
        op = 0; state = 2 /*STATE_1_INIT*/; mode = 1 /*RESET*/; pmode = 3 /*RUN*/;
        wait_line = 1; clear_line = 1; irq = 0; irq_curstate = 0;
        for (auto &e : EF) e = 0;
        rand_seed = seed; gec_calls = 0; memset(ext_count, 0, sizeof(ext_count));
        // device_reset
        IE = 0; Q = 0; DF = 0; P = 0;
        for (int i = 0; i < 16; i++) R[i] = (uint16_t)(rnd() & 0xffff);
        X = 0; D = 0; B = 0; T = 0; I = 0; N = 0;
        // display reset
        strobe = 0; digit_idx = 0;
        for (int i = 0; i < 4; i++) { digit_data[i] = 0; digits[i] = 0; }
        lcd_updates = 0; memset(io_out, 0, sizeof io_out); memset(io_in, 0, sizeof io_in);
        set_q(0);
        // driver machine_reset
        m_reset = true; kp_mux = 0;
        memset(keys, 0, sizeof(keys)); memset(keys_pending, 0, sizeof(keys_pending));
        // scheduler
        basetime = 0; localtime = 0; totalcycles = 0; seqctr = 0; executing = false;
        icount = 0; cycles_running = 0; cycles_stolen = 0;
        for (auto &t : timers) { t.expire = NEVER; t.period = 0; t.enabled = false; t.seq = 0; }
        timer_adjust(T_IRQ, IRQ_PERIOD, IRQ_PERIOD);
        timer_adjust(T_SOUND, ATTO_PER_SEC / 50, ATTO_PER_SEC / 50);      // sound_manager update (50 Hz)
        timer_adjust(T_FRAME, ATTO_PER_SEC / 60, ATTO_PER_SEC / 60);      // screenless frame timer (60 Hz)
        timer_adjust(T_PWM, ATTO_PER_SEC / 60, ATTO_PER_SEC / 60);        // pwm_display frame timer (60 Hz, re-armed in callback)
        irq_taken = 0;
    }

    // ---------------- timers ----------------
    atto_t now() const {
        if (executing) return localtime + (atto_t)(cycles_running - icount) * APS;
        return basetime;
    }
    int head_timer() const {
        int best = -1;
        for (int i = 0; i < T_COUNT; i++) {
            const Timer &t = timers[i];
            if (!t.enabled) continue;
            if (best < 0 || t.expire < timers[best].expire ||
                (t.expire == timers[best].expire && t.seq < timers[best].seq)) best = i;
        }
        return best;
    }
    atto_t head_expire() const { int h = head_timer(); return h < 0 ? NEVER : timers[h].expire; }
    void timer_adjust(int id, atto_t duration, atto_t period = 0) {
        atto_t t0 = now();
        Timer &t = timers[id];
        t.enabled = true;
        t.expire = t0 + duration;
        t.period = period;
        t.seq = ++seqctr;
        if (executing && head_timer() == id) abort_timeslice();
    }
    void abort_timeslice() {
        int delta = icount;
        cycles_stolen += delta;
        cycles_running -= delta;
        icount -= delta;
    }

    // ---------------- display (mephisto_display1_device) ----------------
    void lcd_update() { for (int i = 0; i < 4; i++) digits[i] = digit_data[i]; lcd_updates++; }
    void strobe_w(int st) {
        st = st ? 1 : 0;
        if (st && ~strobe) lcd_update();   // (sic) MAME: ~m_strobe is always non-zero
        strobe = st;
    }
    void set_q(int v) { Q = v; strobe_w(!v); }        // q_cb(...).invert()
    void display_data_w(uint8_t data) {
        digit_data[digit_idx] = strobe ? (uint8_t)~data : data;
        digit_idx = (digit_idx + 1) & 3;
    }

    // ---------------- bus ----------------
    inline uint8_t rd(uint16_t a) { return mem[a]; }
    inline uint8_t rdop(uint16_t a) { return mem[a]; }
    inline void wr(uint16_t a, uint8_t d) {
        if ((a & 0xd000) == 0xd000) { mem[0xd000 | (a & 0xfff)] = d; mem[0xf000 | (a & 0xfff)] = d; }
    }
    // mm1_io: 1 sound_w, 2 keypad_w, 3 board input_r, 4 board mux_w, 5 board led_w, 6 display data_w, 7 unknown_w
    inline uint8_t io_rd(int port) {
        io_in[port & 7]++;
        if (port == 3) return 0xff;      // button board, nothing pressed: 0xff & ~read_rank() == 0xff
        return 0;                         // unmapped
    }
    inline void io_wr(int port, uint8_t d) {
        io_out[port & 7]++;
        switch (port) {
        case 2: kp_mux = (uint8_t)~d; break;
        case 6: display_data_w(d); break;
        default: break;                   // sound / board mux / led / unknown: no CPU-visible effect
        }
    }
    inline int ef3() const { return (keys[0] & kp_mux) ? 1 : 0; }
    inline int ef4() const { return (keys[1] & kp_mux) ? 1 : 0; }

    // ---------------- driver lines ----------------
    inline int clear_r() { int ret = m_reset ? 0 : 1; m_reset = false; return ret; }

    // ---------------- CPU ----------------
    void execute_run();
    void execute_run_fast();
    bool use_fast = true;
    void run_state();
    void fetch_instruction();
    void initialize();
    void execute_instruction();
    void do_interrupt();
    void execute_op();
    void execute_ext();
    void reset_state();

    // ---------------- scheduler ----------------
    void timeslice();
    void execute_timers();
};

} // namespace mm