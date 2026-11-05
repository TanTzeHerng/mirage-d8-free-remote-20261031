#include "mm_core.h"

namespace mm {

enum { ST_FETCH = 0, ST_FETCH2, ST_INIT, ST_EXEC, ST_EXEC2, ST_DMAIN, ST_DMAOUT, ST_INT };
enum { MD_LOAD = 0, MD_RESET, MD_PAUSE, MD_RUN };

void Machine::reset_state() {
    state = ST_INIT;
    op = 0; I = 0; N = 0;
    set_q(0);
    IE = 1;
}

void Machine::initialize() {
    X = 0; P = 0; R[0] = 0;
    icount -= 8;
    state = ST_FETCH;
}

inline void Machine::fetch_instruction() {
    uint16_t addr = R[P]++;
    op = (uint16_t)((op << 8) | rdop(addr));
    I = (op >> 4) & 0x0f;
    N = op & 0x0f;
    icount -= 8;
    // CDP1804 and up: 0x68 for extended opcodes
    state = (op == 0x68) ? ST_FETCH2 : ST_EXEC;
}

inline void Machine::do_interrupt() {
    T = (uint8_t)((X << 4) | P);
    X = 2; P = 1; IE = 0;
    icount -= 8;
    state = ST_FETCH;
    // standard_irq_callback(COSMAC_INPUT_LINE_INT): HOLD_LINE is acknowledged
    if (irq_curstate == 2) { irq = 0; irq_curstate = 0; }
    irq_taken++;
}

#define RAM_R(a) rd(a)
#define RAM_W(a, d) wr(a, d)
#define OPCODE_R(a) rdop(a)

static inline void put_low(uint16_t &r, uint8_t d) { r = (uint16_t)((r & 0xff00) | d); }
static inline void put_high(uint16_t &r, uint8_t d) { r = (uint16_t)((r & 0x00ff) | (d << 8)); }

// CDP1804 extended opcodes (0x68xx) exactly as implemented in MAME 0.238 cosmac.cpp
inline void Machine::execute_ext() {
    const int n = op & 0x0f;
    ext_count[op & 0xff]++;
    switch (op & 0xff) {
    case 0x06: break;                                                       // LDC (no-op in 0.238)
    case 0x08: D = (uint8_t)(rnd() & 0xf); gec_calls++; break;              // GEC (random in 0.238)
    case 0x3e: put_low(R[P], OPCODE_R(R[P])); break;                        // BCI: short_branch(1)
    case 0x3f: R[P]++; break;                                               // BXI: short_branch(0)
    case 0x60: case 0x61: case 0x62: case 0x63: case 0x64: case 0x65: case 0x66: case 0x67:
    case 0x68: case 0x69: case 0x6a: case 0x6b: case 0x6c: case 0x6d: case 0x6e: case 0x6f:
        put_high(R[n], RAM_R(R[X])); R[X]++; put_low(R[n], RAM_R(R[X])); R[X]++; break;   // RLXA
    case 0xa0: case 0xa1: case 0xa2: case 0xa3: case 0xa4: case 0xa5: case 0xa6: case 0xa7:
    case 0xa8: case 0xa9: case 0xaa: case 0xab: case 0xac: case 0xad: case 0xae: case 0xaf:
        RAM_W(R[X], R[n] & 0xff); R[X]--; RAM_W(R[X], (R[n] >> 8) & 0xff); R[X]--; break; // RSXD
    case 0xb0: case 0xb1: case 0xb2: case 0xb3: case 0xb4: case 0xb5: case 0xb6: case 0xb7:
    case 0xb8: case 0xb9: case 0xba: case 0xbb: case 0xbc: case 0xbd: case 0xbe: case 0xbf:
        R[X] = R[n]; break;                                                 // RNX
    case 0xc0: case 0xc1: case 0xc2: case 0xc3: case 0xc4: case 0xc5: case 0xc6: case 0xc7:
    case 0xc8: case 0xc9: case 0xca: case 0xcb: case 0xcc: case 0xcd: case 0xce: case 0xcf:
        put_high(R[n], RAM_R(R[P])); R[P]++; put_low(R[n], RAM_R(R[P])); R[P]++; break;   // RLDI
    default: break;                                                         // und
    }
}

inline void Machine::execute_op() {
    if (op > 0xff) { execute_ext(); return; }
    const uint8_t o = (uint8_t)op;
    const int n = o & 0x0f;
    int res;
    switch (o >> 4) {
    case 0x0:
        if (n == 0) { /* IDL */ }
        else D = RAM_R(R[n]);
        break;
    case 0x1: R[n]++; break;
    case 0x2: R[n]--; break;
    case 0x3: {
        int taken;
        switch (n) {
        case 0x0: taken = 1; break;            // BR
        case 0x1: taken = Q; break;            // BQ
        case 0x2: taken = (D == 0); break;     // BZ
        case 0x3: taken = DF; break;           // BDF
        case 0x4: case 0x5: case 0x6: case 0x7: taken = EF[n & 3]; break;   // B1..B4
        case 0x8: taken = 0; break;            // NBR (SKP)
        case 0x9: taken = !Q; break;           // BNQ
        case 0xa: taken = (D != 0); break;     // BNZ
        case 0xb: taken = !DF; break;          // BNF
        default:  taken = !EF[n & 3]; break;   // BN1..BN4
        }
        if (taken) put_low(R[P], OPCODE_R(R[P]));
        else R[P]++;
        break;
    }
    case 0x4: D = RAM_R(R[n]); R[n]++; break;                 // LDA
    case 0x5: RAM_W(R[n], D); break;                          // STR
    case 0x6:
        if (n == 0) { R[X]++; }                               // IRX
        else if (n < 8) { io_wr(n, RAM_R(R[X])); R[X]++; }    // OUT n
        else { D = io_rd(n & 7); RAM_W(R[X], D); }            // INP n (0x68 is the extended-opcode prefix)
        break;
    case 0x7:
        switch (n) {
        case 0x0: { uint8_t d = RAM_R(R[X]); R[X]++; P = d & 0xf; X = d >> 4; IE = 1; break; } // RET
        case 0x1: { uint8_t d = RAM_R(R[X]); R[X]++; P = d & 0xf; X = d >> 4; IE = 0; break; } // DIS
        case 0x2: D = RAM_R(R[X]); R[X]++; break;                                   // LDXA
        case 0x3: RAM_W(R[X], D); R[X]--; break;                                     // STXD
        case 0x4: res = RAM_R(R[X]) + D + DF; D = res & 0xff; DF = res > 0xff; break; // ADC
        case 0x5: res = RAM_R(R[X]) + (D ^ 0xff) + DF; D = res & 0xff; DF = res > 0xff; break; // SDB
        case 0x6: { int b = DF; DF = D & 1; D >>= 1; D |= b << 7; break; }           // SHRC
        case 0x7: res = D + (RAM_R(R[X]) ^ 0xff) + DF; D = res & 0xff; DF = res > 0xff; break; // SMB
        case 0x8: RAM_W(R[X], T); break;                                             // SAV
        case 0x9: T = (uint8_t)((X << 4) | P); RAM_W(R[2], T); X = P; R[2]--; break; // MARK
        case 0xa: set_q(0); break;                                                   // REQ
        case 0xb: set_q(1); break;                                                   // SEQ
        case 0xc: res = RAM_R(R[P]) + D + DF; D = res & 0xff; DF = res > 0xff; R[P]++; break; // ADCI
        case 0xd: res = RAM_R(R[P]) + (D ^ 0xff) + DF; D = res & 0xff; DF = res > 0xff; R[P]++; break; // SDBI
        case 0xe: { int b = DF; DF = (D >> 7) & 1; D <<= 1; D |= b; break; }        // SHLC
        case 0xf: res = D + (RAM_R(R[P]) ^ 0xff) + DF; D = res & 0xff; DF = res > 0xff; R[P]++; break; // SMBI
        }
        break;
    case 0x8: D = R[n] & 0xff; break;          // GLO
    case 0x9: D = R[n] >> 8; break;            // GHI
    case 0xa: put_low(R[n], D); break;         // PLO
    case 0xb: put_high(R[n], D); break;        // PHI
    case 0xc: {
        // long branch / long skip; executed twice (S1#1, S1#2)
        auto long_branch = [&](int taken) {
            if (taken) {
                if (state == ST_EXEC) { B = OPCODE_R(R[P]); R[P]++; }
                else R[P] = (uint16_t)((B << 8) | OPCODE_R(R[P]));
            } else R[P]++;
        };
        auto long_skip = [&](int taken) { if (taken) R[P]++; };
        switch (n) {
        case 0x0: long_branch(1); break;        // LBR
        case 0x1: long_branch(Q); break;        // LBQ
        case 0x2: long_branch(D == 0); break;   // LBZ
        case 0x3: long_branch(DF); break;       // LBDF
        case 0x4: break;                        // NOP
        case 0x5: long_skip(!Q); break;         // LSNQ
        case 0x6: long_skip(D != 0); break;     // LSNZ
        case 0x7: long_skip(!DF); break;        // LSNF
        case 0x8: long_skip(1); break;          // NLBR / LSKP
        case 0x9: long_branch(!Q); break;       // LBNQ
        case 0xa: long_branch(D != 0); break;   // LBNZ
        case 0xb: long_branch(!DF); break;      // LBNF
        case 0xc: long_skip(IE); break;         // LSIE
        case 0xd: long_skip(Q); break;          // LSQ
        case 0xe: long_skip(D == 0); break;     // LSZ
        case 0xf: long_skip(DF); break;         // LSDF
        }
        break;
    }
    case 0xd: P = n; break;                    // SEP
    case 0xe: X = n; break;                    // SEX
    case 0xf:
        switch (n) {
        case 0x0: D = RAM_R(R[X]); break;                                   // LDX
        case 0x1: D = RAM_R(R[X]) | D; break;                               // OR
        case 0x2: D = RAM_R(R[X]) & D; break;                               // AND
        case 0x3: D = RAM_R(R[X]) ^ D; break;                               // XOR
        case 0x4: res = RAM_R(R[X]) + D; D = res & 0xff; DF = res > 0xff; break;            // ADD
        case 0x5: res = RAM_R(R[X]) + (D ^ 0xff) + 1; D = res & 0xff; DF = res > 0xff; break; // SD
        case 0x6: DF = D & 1; D >>= 1; break;                               // SHR
        case 0x7: res = D + (RAM_R(R[X]) ^ 0xff) + 1; D = res & 0xff; DF = res > 0xff; break; // SM
        case 0x8: D = RAM_R(R[P]); R[P]++; break;                           // LDI
        case 0x9: D = RAM_R(R[P]) | D; R[P]++; break;                       // ORI
        case 0xa: D = RAM_R(R[P]) & D; R[P]++; break;                       // ANI
        case 0xb: D = RAM_R(R[P]) ^ D; R[P]++; break;                       // XRI
        case 0xc: res = RAM_R(R[P]) + D; D = res & 0xff; DF = res > 0xff; R[P]++; break;            // ADI
        case 0xd: res = RAM_R(R[P]) + (D ^ 0xff) + 1; D = res & 0xff; DF = res > 0xff; R[P]++; break; // SDI
        case 0xe: DF = (D >> 7) & 1; D <<= 1; break;                        // SHL
        case 0xf: res = D + (RAM_R(R[P]) ^ 0xff) + 1; D = res & 0xff; DF = res > 0xff; R[P]++; break; // SMI
        }
        break;
    }
}

inline void Machine::execute_instruction() {
    execute_op();
    icount -= 8;
    if (state == ST_EXEC && (op >> 4) == 0xc) state = ST_EXEC2;
    else if (IE && irq) state = ST_INT;
    else if (op != 0) state = ST_FETCH;
}

inline void Machine::run_state() {
    switch (state) {
    case ST_FETCH: op = 0; [[fallthrough]];
    case ST_FETCH2: fetch_instruction(); break;
    case ST_INIT: initialize(); break;
    case ST_EXEC:
        EF[0] = 0; EF[1] = 0; EF[2] = (uint8_t)ef3(); EF[3] = (uint8_t)ef4();   // sample_ef_lines()
        [[fallthrough]];
    case ST_EXEC2: execute_instruction(); break;
    case ST_INT: do_interrupt(); break;
    default: break;
    }
}

void Machine::execute_run() {
    do {
        // sample_wait_clear(): WAIT has no callback (stays 1); CLEAR is read from the driver
        clear_line = (uint8_t)clear_r();
        pmode = mode;
        mode = (uint8_t)((clear_line << 1) | wait_line);
        switch (mode) {
        case MD_RESET:
            reset_state();
            icount--;
            break;
        case MD_PAUSE:
            icount--;
            break;
        case MD_RUN:
            switch (pmode) {
            case MD_RESET: pmode = MD_RUN; state = ST_INIT; run_state(); break;
            case MD_PAUSE: pmode = MD_RUN; state = ST_FETCH; run_state(); break;
            case MD_RUN: run_state(); break;
            default: mode = MD_LOAD; break;
            }
            break;
        default: break;   // LOAD mode cannot occur (WAIT is tied high)
        }
    } while (icount > 0);
}

void Machine::execute_timers() {
    for (;;) {
        int h = head_timer();
        if (h < 0 || timers[h].expire > basetime) break;
        Timer &t = timers[h];
        bool oneshot = (t.period == 0);
        atto_t exp = t.expire;
        uint64_t seq_before = t.seq;
        if (oneshot) t.enabled = false;
        // callback (we are "in a timer callback": now() == expire time)
        atto_t saved_base = basetime;
        basetime = exp;
        switch (h) {
        case T_IRQ:
            // mm1_state::interrupt -> set_input_line(INT, HOLD_LINE) -> synced event (same time)
            irq = 1; irq_curstate = 2;
            break;
        case T_HARNESS:
            if (harness_cb) harness_cb(*this);
            break;
        case T_FRAME:
            // video_manager::frame_update(): Lua periodic_check() first, then MACHINE_NOTIFY_FRAME
            // -> ioport_manager::frame_update() which latches the digital input values
            if (frame_cb) frame_cb(*this);
            for (int p = 0; p < 2; p++) keys[p] = keys_pending[p];
            break;
        default: break;   // sound update, pwm frame: no CPU-visible effect
        }
        basetime = saved_base;
        // reschedule if not modified in the callback
        if (t.seq == seq_before) {
            if (!oneshot) { t.expire = exp + t.period; t.seq = ++seqctr; }
            else { t.expire = NEVER; }
        }
    }
}

void Machine::timeslice() {
    while (basetime < head_expire()) {
        atto_t target = head_expire();
        atto_t quantum_target = basetime + (atto_t)(ATTO_PER_SEC - 1);
        if (quantum_target < target) target = quantum_target;
        atto_t delta = target - localtime;
        if (delta >= APS) {
            int32_t ran = (int32_t)(uint32_t)(((uint64_t)delta >> DIVSHIFT) / DIVISOR);
            cycles_running = ran;
            cycles_stolen = 0;
            icount = cycles_running;
            executing = true;
            if (use_fast) execute_run_fast(); else execute_run();
            executing = false;
            ran -= icount;
            ran -= cycles_stolen;
            totalcycles += (uint64_t)ran;
            localtime += (atto_t)ran * APS;
            if (localtime < target) target = localtime > basetime ? localtime : basetime;
        }
        basetime = target;
    }
    execute_timers();
}

} // namespace mm

// ============================================================================
// Fast path: identical semantics to execute_run() for the steady RUN state.
// Registers are cached in locals; fetch+execute are fused; a 256-way switch is
// used for dispatch.  Falls back to the reference path for reset/init states.
// ============================================================================
namespace mm {

void Machine::execute_run_fast() {
    if (m_reset || mode != MD_RUN || pmode != MD_RUN || state == ST_INIT || (state > ST_EXEC2 && state != ST_INT)) {
        execute_run();
        return;
    }
    // Each loop iteration of the reference execute_run() calls clear_r() (no effect while
    // m_reset is false) and keeps mode = pmode = RUN.  m_reset can only become true from a
    // timer callback, i.e. never during this call.
    uint16_t *const r = R;
    uint8_t *const M = mem;
    int32_t ic = icount;
    uint8_t p = P, x = X, d = D, df = DF, ie = IE;
    uint8_t st = state;
    uint16_t o = op;
    uint16_t pc;
    int res;

#define RD(a) (M[(uint16_t)(a)])
#define WR(a, v) do { uint16_t _a = (uint16_t)(a); uint8_t _v = (uint8_t)(v); \
        if ((_a & 0xd000) == 0xd000) { M[0xd000 | (_a & 0xfff)] = _v; M[0xf000 | (_a & 0xfff)] = _v; } } while (0)
#define SYNC_OUT() do { P = p; X = x; D = d; DF = df; IE = ie; op = o; state = st; icount = ic; } while (0)
#define SYNC_IN()  do { p = P; x = X; d = D; df = DF; ie = IE; ic = icount; } while (0)

    // threaded dispatch table (generated by gen_fast.py) + instruction epilogue
#include "mm_fast_tbl.inc"
#define NEXT(cost) do { ic -= (cost); if (ie && irq) goto do_int; if (ic <= 24) goto leave; \
        o = RD(pc); pc++; r[p] = pc; goto *tbl[o]; } while (0)
#define NEXTP(cost) do { ic -= (cost); r[p] = pc; if (ie && irq) goto do_int; if (ic <= 24) goto leave; \
        o = RD(pc); pc++; r[p] = pc; goto *tbl[o]; } while (0)

    for (;;) {
        if (st == ST_INT) {
            T = (uint8_t)((x << 4) | p);
            x = 2; p = 1; ie = 0;
            ic -= 8;
            st = ST_FETCH;
            if (irq_curstate == 2) { irq = 0; irq_curstate = 0; }
            irq_taken++;
            if (ic <= 0) break;
            continue;
        }
        if (st == ST_FETCH && ic > 24) {
            // streamlined path: whole instructions, no mid-instruction slice end is possible (ic > 24)
            pc = r[p];
            o = RD(pc); pc++; r[p] = pc;
            goto *tbl[o];
#include "mm_fast_ops.inc"
        do_int:
            T = (uint8_t)((x << 4) | p);
            x = 2; p = 1; ie = 0;
            ic -= 8;
            if (irq_curstate == 2) { irq = 0; irq_curstate = 0; }
            irq_taken++;
            st = ST_FETCH;
            if (ic <= 0) goto done;
            if (ic <= 24) goto leave_st;
            pc = r[p];
            o = RD(pc); pc++; r[p] = pc;
            goto *tbl[o];
        leave:
            st = ST_FETCH;
        leave_st:
            continue;
        }
        if (st == ST_FETCH) {
            uint16_t a = r[p]++;
            o = RD(a);
            ic -= 8;
            st = (o == 0x68) ? ST_FETCH2 : ST_EXEC;
            if (ic <= 0) break;
        }
        if (st == ST_FETCH2) {
            uint16_t a = r[p]++;
            o = (uint16_t)((o << 8) | RD(a));
            ic -= 8;
            st = ST_EXEC;
            if (ic <= 0) break;
        }
        // ---- execute (S1#1) ----
        const int n = o & 0x0f;
        bool islong = false;
        if (o > 0xff) {
            // CDP1804 extended opcode (0x68xx): fetch + fetch2 + execute, no second execute cycle
            ext_count[o & 0xff]++;
            switch (o & 0xff) {
            case 0x06: break;                                                   // LDC
            case 0x08: d = (uint8_t)(rnd() & 0xf); gec_calls++; break;          // GEC
            case 0x3e: r[p] = (uint16_t)((r[p] & 0xff00) | RD(r[p])); break;    // BCI
            case 0x3f: r[p]++; break;                                           // BXI
            case 0x60: case 0x61: case 0x62: case 0x63: case 0x64: case 0x65: case 0x66: case 0x67:
            case 0x68: case 0x69: case 0x6a: case 0x6b: case 0x6c: case 0x6d: case 0x6e: case 0x6f:
                r[n] = (uint16_t)((r[n] & 0x00ff) | (RD(r[x]) << 8)); r[x]++;
                r[n] = (uint16_t)((r[n] & 0xff00) | RD(r[x])); r[x]++; break;   // RLXA
            case 0xa0: case 0xa1: case 0xa2: case 0xa3: case 0xa4: case 0xa5: case 0xa6: case 0xa7:
            case 0xa8: case 0xa9: case 0xaa: case 0xab: case 0xac: case 0xad: case 0xae: case 0xaf:
                WR(r[x], r[n] & 0xff); r[x]--; WR(r[x], (r[n] >> 8) & 0xff); r[x]--; break;   // RSXD
            case 0xb0: case 0xb1: case 0xb2: case 0xb3: case 0xb4: case 0xb5: case 0xb6: case 0xb7:
            case 0xb8: case 0xb9: case 0xba: case 0xbb: case 0xbc: case 0xbd: case 0xbe: case 0xbf:
                r[x] = r[n]; break;                                             // RNX
            case 0xc0: case 0xc1: case 0xc2: case 0xc3: case 0xc4: case 0xc5: case 0xc6: case 0xc7:
            case 0xc8: case 0xc9: case 0xca: case 0xcb: case 0xcc: case 0xcd: case 0xce: case 0xcf:
                r[n] = (uint16_t)((r[n] & 0x00ff) | (RD(r[p]) << 8)); r[p]++;
                r[n] = (uint16_t)((r[n] & 0xff00) | RD(r[p])); r[p]++; break;   // RLDI
            default: break;                                                     // und
            }
            ic -= 8;
            if (ie && irq) st = ST_INT; else st = ST_FETCH;
            if (ic <= 0) break;
            continue;
        }
        switch (o) {
        case 0x00: break;                                                      // IDL
        case 0x01: case 0x02: case 0x03: case 0x04: case 0x05: case 0x06: case 0x07:
        case 0x08: case 0x09: case 0x0a: case 0x0b: case 0x0c: case 0x0d: case 0x0e: case 0x0f:
            d = RD(r[n]); break;                                               // LDN
        case 0x10: case 0x11: case 0x12: case 0x13: case 0x14: case 0x15: case 0x16: case 0x17:
        case 0x18: case 0x19: case 0x1a: case 0x1b: case 0x1c: case 0x1d: case 0x1e: case 0x1f:
            r[n]++; break;
        case 0x20: case 0x21: case 0x22: case 0x23: case 0x24: case 0x25: case 0x26: case 0x27:
        case 0x28: case 0x29: case 0x2a: case 0x2b: case 0x2c: case 0x2d: case 0x2e: case 0x2f:
            r[n]--; break;
#define SBR(cond) { if (cond) r[p] = (uint16_t)((r[p] & 0xff00) | RD(r[p])); else r[p]++; } break
        case 0x30: SBR(1);
        case 0x31: SBR(Q);
        case 0x32: SBR(d == 0);
        case 0x33: SBR(df);
        case 0x34: case 0x35: SBR(0);                           // B1, B2: EF1/EF2 not connected
        case 0x36: SBR(ef3());                                  // B3
        case 0x37: SBR(ef4());                                  // B4
        case 0x38: SBR(0);                                      // SKP
        case 0x39: SBR(!Q);
        case 0x3a: SBR(d != 0);
        case 0x3b: SBR(!df);
        case 0x3c: case 0x3d: SBR(1);                           // BN1, BN2
        case 0x3e: SBR(!ef3());                                 // BN3
        case 0x3f: SBR(!ef4());                                 // BN4
        case 0x40: case 0x41: case 0x42: case 0x43: case 0x44: case 0x45: case 0x46: case 0x47:
        case 0x48: case 0x49: case 0x4a: case 0x4b: case 0x4c: case 0x4d: case 0x4e: case 0x4f:
            d = RD(r[n]); r[n]++; break;                                       // LDA
        case 0x50: case 0x51: case 0x52: case 0x53: case 0x54: case 0x55: case 0x56: case 0x57:
        case 0x58: case 0x59: case 0x5a: case 0x5b: case 0x5c: case 0x5d: case 0x5e: case 0x5f:
            WR(r[n], d); break;                                                // STR
        case 0x60: r[x]++; break;                                              // IRX
        case 0x61: case 0x62: case 0x63: case 0x64: case 0x65: case 0x66: case 0x67: {   // OUT
            uint8_t v = RD(r[x]);
            SYNC_OUT(); io_wr(n, v); SYNC_IN();
            r[x]++; break;
        }
        case 0x69: case 0x6a: case 0x6b: case 0x6c: case 0x6d: case 0x6e: case 0x6f: { // INP (0x68 is the prefix)
            SYNC_OUT(); uint8_t v = io_rd(n & 7); SYNC_IN();
            d = v; WR(r[x], d); break;
        }
        case 0x70: { uint8_t v = RD(r[x]); r[x]++; p = v & 0xf; x = v >> 4; ie = 1; break; }   // RET
        case 0x71: { uint8_t v = RD(r[x]); r[x]++; p = v & 0xf; x = v >> 4; ie = 0; break; }   // DIS
        case 0x72: d = RD(r[x]); r[x]++; break;                                 // LDXA
        case 0x73: WR(r[x], d); r[x]--; break;                                  // STXD
        case 0x74: res = RD(r[x]) + d + df; d = (uint8_t)res; df = res > 0xff; break;              // ADC
        case 0x75: res = RD(r[x]) + (d ^ 0xff) + df; d = (uint8_t)res; df = res > 0xff; break;     // SDB
        case 0x76: { int b = df; df = d & 1; d = (uint8_t)((d >> 1) | (b << 7)); break; }         // SHRC
        case 0x77: res = d + (RD(r[x]) ^ 0xff) + df; d = (uint8_t)res; df = res > 0xff; break;     // SMB
        case 0x78: WR(r[x], T); break;                                          // SAV
        case 0x79: T = (uint8_t)((x << 4) | p); WR(r[2], T); x = p; r[2]--; break;               // MARK
        case 0x7a: SYNC_OUT(); set_q(0); break;                                 // REQ
        case 0x7b: SYNC_OUT(); set_q(1); break;                                 // SEQ
        case 0x7c: res = RD(r[p]) + d + df; d = (uint8_t)res; df = res > 0xff; r[p]++; break;           // ADCI
        case 0x7d: res = RD(r[p]) + (d ^ 0xff) + df; d = (uint8_t)res; df = res > 0xff; r[p]++; break;  // SDBI
        case 0x7e: { int b = df; df = (d >> 7) & 1; d = (uint8_t)((d << 1) | b); break; }        // SHLC
        case 0x7f: res = d + (RD(r[p]) ^ 0xff) + df; d = (uint8_t)res; df = res > 0xff; r[p]++; break;  // SMBI
        case 0x80: case 0x81: case 0x82: case 0x83: case 0x84: case 0x85: case 0x86: case 0x87:
        case 0x88: case 0x89: case 0x8a: case 0x8b: case 0x8c: case 0x8d: case 0x8e: case 0x8f:
            d = (uint8_t)r[n]; break;                                           // GLO
        case 0x90: case 0x91: case 0x92: case 0x93: case 0x94: case 0x95: case 0x96: case 0x97:
        case 0x98: case 0x99: case 0x9a: case 0x9b: case 0x9c: case 0x9d: case 0x9e: case 0x9f:
            d = (uint8_t)(r[n] >> 8); break;                                    // GHI
        case 0xa0: case 0xa1: case 0xa2: case 0xa3: case 0xa4: case 0xa5: case 0xa6: case 0xa7:
        case 0xa8: case 0xa9: case 0xaa: case 0xab: case 0xac: case 0xad: case 0xae: case 0xaf:
            r[n] = (uint16_t)((r[n] & 0xff00) | d); break;                      // PLO
        case 0xb0: case 0xb1: case 0xb2: case 0xb3: case 0xb4: case 0xb5: case 0xb6: case 0xb7:
        case 0xb8: case 0xb9: case 0xba: case 0xbb: case 0xbc: case 0xbd: case 0xbe: case 0xbf:
            r[n] = (uint16_t)((r[n] & 0x00ff) | (d << 8)); break;               // PHI
        case 0xc0: case 0xc1: case 0xc2: case 0xc3: case 0xc4: case 0xc5: case 0xc6: case 0xc7:
        case 0xc8: case 0xc9: case 0xca: case 0xcb: case 0xcc: case 0xcd: case 0xce: case 0xcf:
            islong = true; break;
        case 0xd0: case 0xd1: case 0xd2: case 0xd3: case 0xd4: case 0xd5: case 0xd6: case 0xd7:
        case 0xd8: case 0xd9: case 0xda: case 0xdb: case 0xdc: case 0xdd: case 0xde: case 0xdf:
            p = (uint8_t)n; break;                                              // SEP
        case 0xe0: case 0xe1: case 0xe2: case 0xe3: case 0xe4: case 0xe5: case 0xe6: case 0xe7:
        case 0xe8: case 0xe9: case 0xea: case 0xeb: case 0xec: case 0xed: case 0xee: case 0xef:
            x = (uint8_t)n; break;                                              // SEX
        case 0xf0: d = RD(r[x]); break;                                         // LDX
        case 0xf1: d = RD(r[x]) | d; break;                                     // OR
        case 0xf2: d = RD(r[x]) & d; break;                                     // AND
        case 0xf3: d = RD(r[x]) ^ d; break;                                     // XOR
        case 0xf4: res = RD(r[x]) + d; d = (uint8_t)res; df = res > 0xff; break;                  // ADD
        case 0xf5: res = RD(r[x]) + (d ^ 0xff) + 1; d = (uint8_t)res; df = res > 0xff; break;     // SD
        case 0xf6: df = d & 1; d >>= 1; break;                                  // SHR
        case 0xf7: res = d + (RD(r[x]) ^ 0xff) + 1; d = (uint8_t)res; df = res > 0xff; break;     // SM
        case 0xf8: d = RD(r[p]); r[p]++; break;                                 // LDI
        case 0xf9: d = RD(r[p]) | d; r[p]++; break;                             // ORI
        case 0xfa: d = RD(r[p]) & d; r[p]++; break;                             // ANI
        case 0xfb: d = RD(r[p]) ^ d; r[p]++; break;                             // XRI
        case 0xfc: res = RD(r[p]) + d; d = (uint8_t)res; df = res > 0xff; r[p]++; break;                // ADI
        case 0xfd: res = RD(r[p]) + (d ^ 0xff) + 1; d = (uint8_t)res; df = res > 0xff; r[p]++; break;   // SDI
        case 0xfe: df = (d >> 7) & 1; d = (uint8_t)(d << 1); break;             // SHL
        case 0xff: res = d + (RD(r[p]) ^ 0xff) + 1; d = (uint8_t)res; df = res > 0xff; r[p]++; break;   // SMI
        }
        if (islong) {
            // S1#1 / S1#2 for the 0xCx group.  st tells which half we're in.
            const int c = o & 0x0f;
            int taken;
            bool isbranch;
            switch (c) {
            case 0x0: isbranch = true; taken = 1; break;
            case 0x1: isbranch = true; taken = Q; break;
            case 0x2: isbranch = true; taken = d == 0; break;
            case 0x3: isbranch = true; taken = df; break;
            case 0x4: isbranch = false; taken = 0; break;     // NOP
            case 0x5: isbranch = false; taken = !Q; break;
            case 0x6: isbranch = false; taken = d != 0; break;
            case 0x7: isbranch = false; taken = !df; break;
            case 0x8: isbranch = false; taken = 1; break;
            case 0x9: isbranch = true; taken = !Q; break;
            case 0xa: isbranch = true; taken = d != 0; break;
            case 0xb: isbranch = true; taken = !df; break;
            case 0xc: isbranch = false; taken = ie; break;
            case 0xd: isbranch = false; taken = Q; break;
            case 0xe: isbranch = false; taken = d == 0; break;
            default:  isbranch = false; taken = df; break;
            }
            if (st == ST_EXEC) {
                if (isbranch) { if (taken) { B = RD(r[p]); r[p]++; } else r[p]++; }
                else if (taken) r[p]++;
                ic -= 8;
                st = ST_EXEC2;
                if (ic <= 0) break;
                // second half (condition re-evaluated with unchanged state)
            }
            if (isbranch) { if (taken) r[p] = (uint16_t)((B << 8) | RD(r[p])); else r[p]++; }
            else if (taken) r[p]++;
            ic -= 8;
        } else {
            ic -= 8;
        }
        // state transition after execute
        if (ie && irq) st = ST_INT;
        else if (o != 0) st = ST_FETCH;
        else st = ST_EXEC;          // IDL: stay in execute
        if (ic <= 0) break;
    }
done:
    SYNC_OUT();
#undef NEXTP
#undef NEXT
#undef RD
#undef WR
#undef SYNC_OUT
#undef SYNC_IN
#undef SBR
}

} // namespace mm