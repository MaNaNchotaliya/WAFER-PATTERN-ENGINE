// wpe_engine.sv - early wafer-map pattern engine.
//
// Dies arrive in prober order (x, y, pass/fail, probe site). For each die the engine
//   1. rejects it if it is outside the W x H map or was already tested,
//   2. reads its 8 neighbours from a 256 x 256 x 2-bit map (tested, failed) and counts
//      fail-fail and fail-pass pairs per direction,
//   3. adds it to its zone (5 rings x 8 sectors), ring, site and wafer counters.
// After every `chk` accepted dies and after the last die it runs a checkpoint:
// 39 features through one shared divider, the 576-tree ensemble, the early-call rule
// and the probe-site check, then emits one record (ck_valid + ck).
// The exact behaviour is model/wpe_model.py; scripts/run_regression.py compares them.
module wpe_engine
  import wpe_pkg::*;
#(
  parameter NODE_FILE = "wpe_nodes.mem",
  parameter LEAF_FILE = "wpe_leaves.mem"
)(
  input  logic             clk,
  input  logic             rst,
  // configuration registers (write between wafers)
  input  logic             cfg_we,
  input  logic [CAW-1:0]   cfg_addr,
  input  logic [CDW-1:0]   cfg_wdata,
  input  logic             start,       // one-clock pulse: new wafer
  // die stream
  input  logic             s_valid,
  output logic             s_ready,
  input  logic [XYW-1:0]   s_x,
  input  logic [XYW-1:0]   s_y,
  input  logic             s_fail,
  input  logic [2:0]       s_site,
  input  logic             s_last,
  // status and checkpoint records
  output logic             busy,
  output logic             done,
  output logic             ck_valid,
  output ck_rec_t          ck
);

  // ================================================================ configuration
  logic [DW-1:0]          c_w, c_h, c_cx2, c_cy2;
  logic [7:0]             c_wx, c_wy;
  logic [3:0][THW-1:0]    c_thr;
  logic [CW-1:0]          c_ntot;
  logic [NR-1:0][CW-1:0]  c_nr;
  logic [7:0]             c_chk;
  logic [3:0]             c_persist;
  logic [QB:0]            c_minfrac;
  logic [NCLS-1:0][15:0]  c_margin;
  logic [3:0]             c_nsites;
  logic [CW-1:0]          c_sitemin;
  logic signed [FW-1:0]   c_sitedelta;

  always_ff @(posedge clk) begin
    if (rst) begin
      c_w <= '0; c_h <= '0; c_cx2 <= '0; c_cy2 <= '0; c_wx <= '0; c_wy <= '0; c_thr <= '0;
      c_ntot <= '0; c_nr <= '0; c_chk <= '0; c_persist <= '0; c_minfrac <= '0; c_margin <= '0;
      c_nsites <= '0; c_sitemin <= '0; c_sitedelta <= '0;
    end else if (cfg_we) begin
      case (cfg_addr)
        R_W:         c_w         <= cfg_wdata[DW-1:0];
        R_H:         c_h         <= cfg_wdata[DW-1:0];
        R_CX2:       c_cx2       <= cfg_wdata[DW-1:0];
        R_CY2:       c_cy2       <= cfg_wdata[DW-1:0];
        R_WX:        c_wx        <= cfg_wdata[7:0];
        R_WY:        c_wy        <= cfg_wdata[7:0];
        R_NTOT:      c_ntot      <= cfg_wdata[CW-1:0];
        R_CHK:       c_chk       <= cfg_wdata[7:0];
        R_PERSIST:   c_persist   <= cfg_wdata[3:0];
        R_MINFRAC:   c_minfrac   <= cfg_wdata[QB:0];
        R_NSITES:    c_nsites    <= cfg_wdata[3:0];
        R_SITEMIN:   c_sitemin   <= cfg_wdata[CW-1:0];
        R_SITEDELTA: c_sitedelta <= $signed(cfg_wdata[FW-1:0]);
        default: begin
          for (int k = 0; k < 4; k++)
            if (cfg_addr == R_THR0 + CAW'(k)) c_thr[k] <= cfg_wdata[THW-1:0];
          for (int k = 0; k < NR; k++)
            if (cfg_addr == R_NR0 + CAW'(k)) c_nr[k] <= cfg_wdata[CW-1:0];
          for (int k = 0; k < NCLS; k++)
            if (cfg_addr == R_MARGIN0 + CAW'(k)) c_margin[k] <= cfg_wdata[15:0];
        end
      endcase
    end
  end

  // ================================================================ state
  typedef enum logic [4:0] {
    S_IDLE, S_CLEAR, S_WAIT, S_CHECK, S_SCAN, S_UPDATE, S_POST,
    S_FEAT_I, S_FEAT_W, S_ZONE, S_ZONE_W, S_ZONE_END, S_SCONC_W,
    S_GBT, S_GBT_W, S_TOP, S_DECIDE, S_SITE, S_SITE_W1, S_SITE_I2, S_SITE_W2, S_EMIT, S_DONE
  } state_t;
  state_t st;

  // current die
  logic [XYW-1:0] d_x, d_y;
  logic           d_fail, d_last;
  logic [2:0]     d_site;
  logic           periodic;

  // counters
  logic [CW-1:0]            cnt_t, cnt_f, n_dup, n_oor;
  logic [NR-1:0][CW-1:0]    rt, rf;            // per ring: tested, failed
  logic [3:0][CW-1:0]       pff, pxx;          // per direction: fail-fail, fail-pass pairs
  logic [NSITE-1:0][CW-1:0] sit, sif;          // per site: tested, failed
  logic [7:0]               ck_cnt;

  // decision state
  logic signed [SCW-1:0] best, second;
  logic [CLW-1:0]        bi, tk;
  logic [CLW-1:0]        top_r;
  logic [MGW-1:0]        margin_r;
  logic [CLW-1:0]        run_cls;
  logic [3:0]            run_len;
  logic                  called;
  logic [CLW-1:0]        call_cls;
  logic [CW-1:0]         call_ndies;
  logic                  site_flag;
  logic [2:0]            site_id;
  logic [CW-1:0]         site_ndies;
  logic [QB:0]           rs_q;
  logic [3:0]            site_i;       // probe site being checked
  logic [3:0]            n_site_eff;
  logic                  site_ok;

  // ================================================================ die map (2 bits per position)
  (* ram_style = "block" *) logic [1:0] map_mem [0:(1 << (2 * XYW)) - 1];
  logic [2*XYW-1:0] map_addr;
  logic             map_we;
  logic [1:0]       map_wd, map_q;
  always_ff @(posedge clk) begin
    if (map_we) map_mem[map_addr] <= map_wd;
    map_q <= map_mem[map_addr];
  end

  // ================================================================ zone counters (distributed RAM)
  logic [CW-1:0] tz_mem [0:NZ-1];
  logic [CW-1:0] fz_mem [0:NZ-1];
  logic [5:0]    zw_addr, zr_addr;
  logic          zw_en;
  logic [CW-1:0] tz_wd, fz_wd, tz_rd, fz_rd;
  always_ff @(posedge clk) begin
    if (zw_en) begin
      tz_mem[zw_addr] <= tz_wd;
      fz_mem[zw_addr] <= fz_wd;
    end
  end
  assign tz_rd = tz_mem[zr_addr];
  assign fz_rd = fz_mem[zr_addr];

  // ================================================================ zone of the current die
  logic [2:0] z_ring, z_sect;
  wpe_zone u_zone (
    .clk, .x(d_x), .y(d_y), .cx2(c_cx2), .cy2(c_cy2), .wx(c_wx), .wy(c_wy), .thr(c_thr),
    .ring(z_ring), .sect(z_sect)
  );

  // ================================================================ neighbour scan
  // position 0 is the die itself (duplicate check), 1..8 the neighbours:
  //   (-1,0) (1,0) horizontal, (0,-1) (0,1) vertical, (-1,-1) (1,1) "\", (1,-1) (-1,1) "/"
  logic [3:0] scan_j;                  // position issued this clock
  logic [3:0] pend_j;                  // position whose map data is in map_q
  logic       pend_inb;
  logic [1:0] pend_dir;
  logic signed [1:0] ox, oy;
  logic [1:0] odir;
  always_comb begin
    case (scan_j)
      4'd1:    begin ox = -2'sd1; oy =  2'sd0; odir = 2'd0; end
      4'd2:    begin ox =  2'sd1; oy =  2'sd0; odir = 2'd0; end
      4'd3:    begin ox =  2'sd0; oy = -2'sd1; odir = 2'd1; end
      4'd4:    begin ox =  2'sd0; oy =  2'sd1; odir = 2'd1; end
      4'd5:    begin ox = -2'sd1; oy = -2'sd1; odir = 2'd2; end
      4'd6:    begin ox =  2'sd1; oy =  2'sd1; odir = 2'd2; end
      4'd7:    begin ox =  2'sd1; oy = -2'sd1; odir = 2'd3; end
      4'd8:    begin ox = -2'sd1; oy =  2'sd1; odir = 2'd3; end
      default: begin ox =  2'sd0; oy =  2'sd0; odir = 2'd0; end
    endcase
  end
  logic signed [XYW+1:0] nx, ny;
  logic                  n_inb;
  assign nx    = $signed({2'b00, d_x}) + ox;
  assign ny    = $signed({2'b00, d_y}) + oy;
  assign n_inb = (nx >= 0) && (nx < $signed({1'b0, c_w})) && (ny >= 0) && (ny < $signed({1'b0, c_h}));

  // clear pointers
  logic [XYW-1:0] clr_x, clr_y;
  logic [5:0]     clr_z;
  logic           clr_map_done, clr_z_done;

  always_comb begin
    map_addr = {d_y, d_x};
    map_we   = 1'b0;
    map_wd   = {1'b1, d_fail};
    case (st)
      S_CLEAR:  begin map_addr = {clr_y, clr_x}; map_we = !clr_map_done; map_wd = 2'b00; end
      S_SCAN:   map_addr = {ny[XYW-1:0], nx[XYW-1:0]};
      S_UPDATE: map_we = 1'b1;
      default:  ;
    endcase
  end

  // ================================================================ divider and features
  logic            div_start, div_busy, div_done;
  logic [DVW-1:0]  div_a, div_b;
  logic [QB:0]     div_q;
  wpe_div u_div (.clk, .rst, .start(div_start), .a(div_a), .b(div_b),
                 .busy(div_busy), .done(div_done), .q(div_q));

  logic signed [NF-1:0][FW-1:0] feat;
  logic [4:0]    step;                 // feature step (12 = zone loop)
  logic [5:0]    zi;                   // zone index in the loop
  logic [QB:0]   f_q;                  // wafer fail rate, kept for the contrasts
  logic [QB:0]   hi, lo, qmax, qmin;
  logic          anyok;
  logic [CW-1:0] maxf, maxfz;
  logic [DVW-1:0] fft, xt;             // pair totals at the checkpoint
  logic [2:0]    ring_i;
  assign ring_i = zi[5:3];

  function automatic logic signed [FW-1:0] uq(input logic [QB:0] v);
    return $signed({{(FW - QB - 1){1'b0}}, v});
  endfunction

  // divider operands for each step
  always_comb begin
    div_a = '0;
    div_b = '0;
    case (st)
      S_FEAT_I: begin
        if (step == 5'd0) begin
          div_a = DVW'(cnt_t);  div_b = DVW'(c_ntot);
        end else if (step == 5'd1) begin
          div_a = DVW'(cnt_f);  div_b = DVW'(cnt_t);
        end else if (step <= 5'd6) begin
          div_a = DVW'(rf[step - 5'd2]);  div_b = DVW'(rt[step - 5'd2]);
        end else if (step <= 5'd11) begin
          div_a = DVW'(rt[step - 5'd7]);  div_b = DVW'(c_nr[step - 5'd7]);
        end else if (step == 5'd13) begin
          div_a = DVW'(maxfz);  div_b = DVW'(cnt_f);
        end else if (step == 5'd14) begin
          div_a = DVW'({fft, 1'b0});  div_b = DVW'({fft, 1'b0}) + xt;
        end else if (step <= 5'd18) begin
          div_a = DVW'({pff[step - 5'd15], 1'b0});
          div_b = DVW'({pff[step - 5'd15], 1'b0}) + DVW'(pxx[step - 5'd15]);
        end else begin
          div_a = fft;  div_b = DVW'({cnt_f, 2'b00});
        end
      end
      S_ZONE:     begin div_a = DVW'(fz_rd); div_b = DVW'(tz_rd); end
      S_ZONE_END: begin div_a = DVW'(maxf);  div_b = DVW'(rf[ring_i]); end
      S_SITE:     begin div_a = DVW'(sif[site_i[2:0]]); div_b = DVW'(sit[site_i[2:0]]); end
      S_SITE_I2:  begin div_a = DVW'(cnt_f - sif[site_i[2:0]]); div_b = DVW'(cnt_t - sit[site_i[2:0]]); end
      default: ;
    endcase
  end

  assign n_site_eff = (c_nsites > 4'(NSITE)) ? 4'(NSITE) : c_nsites;
  assign site_ok    = (sit[site_i[2:0]] >= c_sitemin) && (cnt_t > sit[site_i[2:0]]);

  always_comb begin
    div_start = 1'b0;
    case (st)
      S_FEAT_I:   div_start = 1'b1;
      S_ZONE:     div_start = (tz_rd >= CW'(TMIN));
      S_ZONE_END: div_start = (zi[2:0] == 3'd7);
      S_SITE:     div_start = !site_flag && (c_nsites != 4'd0) && (site_i < n_site_eff) && site_ok;
      S_SITE_I2:  div_start = 1'b1;
      default: ;
    endcase
  end

  // ================================================================ tree ensemble
  logic                          gbt_start, gbt_busy, gbt_done;
  logic [FIW-1:0]                gbt_fidx;
  logic signed [FW-1:0]          gbt_fval;
  logic signed [NCLS-1:0][SCW-1:0] score;
  assign gbt_start = (st == S_GBT);
  assign gbt_fval  = (gbt_fidx < FIW'(NF)) ? $signed(feat[gbt_fidx]) : '0;
  wpe_gbt #(.NODE_FILE(NODE_FILE), .LEAF_FILE(LEAF_FILE)) u_gbt (
    .clk, .rst, .start(gbt_start), .busy(gbt_busy), .done(gbt_done),
    .fidx(gbt_fidx), .fval(gbt_fval), .score(score)
  );

  // ================================================================ decision rule

  logic signed [MGW-1:0] margin_c;
  logic                  ok_c;
  logic [3:0]            run_len_c, persist_eff;
  logic [CLW-1:0]        run_cls_c;
  assign margin_c    = MGW'(best) - MGW'(second);
  assign persist_eff = (c_persist == 4'd0) ? 4'd1 : c_persist;
  always_comb begin
    ok_c = (bi != '0) && (margin_c >= $signed({{(MGW - 16){1'b0}}, c_margin[bi]}))
           && ($signed(feat[F_FRAC]) >= $signed({1'b0, c_minfrac}));
    run_cls_c = run_cls;
    run_len_c = 4'd0;
    if (ok_c) begin
      if (run_len != 4'd0 && bi == run_cls)
        run_len_c = (run_len == 4'(RUN_MAX)) ? run_len : run_len + 4'd1;
      else begin
        run_cls_c = bi;
        run_len_c = 4'd1;
      end
    end
  end

  // ================================================================ main FSM
  assign s_ready  = (st == S_WAIT);
  assign busy     = (st != S_IDLE) && (st != S_DONE);
  assign done     = (st == S_DONE);
  assign ck_valid = (st == S_EMIT);

  always_comb begin
    zr_addr = (st == S_UPDATE) ? {z_ring, z_sect} : zi;
    zw_en   = 1'b0;
    zw_addr = {z_ring, z_sect};
    tz_wd   = tz_rd + CW'(1);
    fz_wd   = fz_rd + CW'(d_fail);
    if (st == S_CLEAR) begin
      zw_en   = !clr_z_done;
      zw_addr = clr_z;
      tz_wd   = '0;
      fz_wd   = '0;
    end else if (st == S_UPDATE) begin
      zw_en = 1'b1;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      st <= S_IDLE;
    end else begin
      case (st)
        // ---------------------------------------------------------- wafer start
        S_IDLE, S_DONE: if (start) begin
          cnt_t <= '0; cnt_f <= '0; n_dup <= '0; n_oor <= '0; ck_cnt <= '0;
          rt <= '0; rf <= '0; pff <= '0; pxx <= '0; sit <= '0; sif <= '0;
          run_cls <= '0; run_len <= '0; called <= 1'b0; call_cls <= '0; call_ndies <= '0;
          site_flag <= 1'b0; site_id <= '0; site_ndies <= '0; top_r <= '0; margin_r <= '0;
          d_last <= 1'b0;
          clr_x <= '0; clr_y <= '0; clr_z <= '0;
          clr_map_done <= (c_w == '0) || (c_h == '0);
          clr_z_done <= 1'b0;
          st <= S_CLEAR;
        end
        S_CLEAR: begin
          if (!clr_map_done) begin
            if (DW'(clr_x) == c_w - DW'(1)) begin
              clr_x <= '0;
              if (DW'(clr_y) == c_h - DW'(1)) clr_map_done <= 1'b1;
              else clr_y <= clr_y + XYW'(1);
            end else begin
              clr_x <= clr_x + XYW'(1);
            end
          end
          if (!clr_z_done) begin
            if (clr_z == 6'(NZ - 1)) clr_z_done <= 1'b1;
            else clr_z <= clr_z + 6'd1;
          end
          if (clr_map_done && clr_z_done) st <= S_WAIT;
        end
        // ---------------------------------------------------------- one die
        S_WAIT: if (s_valid) begin
          d_x <= s_x; d_y <= s_y; d_fail <= s_fail; d_site <= s_site; d_last <= s_last;
          periodic <= 1'b0;
          st <= S_CHECK;
        end
        S_CHECK: begin
          if (DW'(d_x) >= c_w || DW'(d_y) >= c_h) begin
            n_oor <= n_oor + CW'(1);
            st <= S_POST;
          end else begin
            pend_j <= '0;            // map_addr = own position this clock
            scan_j <= 4'd1;
            st <= S_SCAN;
          end
        end
        S_SCAN: begin
          // data for position pend_j arrived; issue position scan_j
          if (pend_j == 4'd0 && map_q[1]) begin
            n_dup <= n_dup + CW'(1);  // already tested: ignore the die
            st <= S_POST;
          end else begin
            if (pend_j != 4'd0 && pend_inb && map_q[1]) begin
              if (d_fail && map_q[0])       pff[pend_dir] <= pff[pend_dir] + CW'(1);
              else if (d_fail != map_q[0])  pxx[pend_dir] <= pxx[pend_dir] + CW'(1);
            end
            pend_j   <= scan_j;
            pend_inb <= n_inb;
            pend_dir <= odir;
            scan_j   <= scan_j + 4'd1;
            if (pend_j == 4'd8) st <= S_UPDATE;
          end
        end
        S_UPDATE: begin
          // map write and zone read-modify-write happen through the comb blocks above
          rt[z_ring] <= rt[z_ring] + CW'(1);
          rf[z_ring] <= rf[z_ring] + CW'(d_fail);
          cnt_t <= cnt_t + CW'(1);
          cnt_f <= cnt_f + CW'(d_fail);
          sit[d_site] <= sit[d_site] + CW'(1);
          sif[d_site] <= sif[d_site] + CW'(d_fail);
          if (c_chk != 8'd0) begin
            if (ck_cnt + 8'd1 == c_chk) begin
              ck_cnt   <= '0;
              periodic <= 1'b1;
            end else begin
              ck_cnt <= ck_cnt + 8'd1;
            end
          end
          st <= S_POST;
        end
        S_POST: begin
          if (periodic || d_last) begin
            fft  <= DVW'(pff[0]) + DVW'(pff[1]) + DVW'(pff[2]) + DVW'(pff[3]);
            xt   <= DVW'(pxx[0]) + DVW'(pxx[1]) + DVW'(pxx[2]) + DVW'(pxx[3]);
            step <= '0;
            st   <= S_FEAT_I;
          end else begin
            st <= S_WAIT;
          end
        end
        // ---------------------------------------------------------- features
        S_FEAT_I: st <= S_FEAT_W;
        S_FEAT_W: if (div_done) begin
          if (step == 5'd0) begin
            feat[F_FRAC] <= uq(div_q);
          end else if (step == 5'd1) begin
            feat[F_F] <= uq(div_q);
            f_q <= div_q;
          end else if (step <= 5'd6) begin
            feat[F_FR + step - 2] <= uq(div_q);
            feat[F_DR + step - 2] <= uq(div_q) - uq(f_q);
          end else if (step <= 5'd11) begin
            feat[F_COV + step - 7] <= uq(div_q);
          end else if (step == 5'd13) begin
            feat[F_ZCONC] <= uq(div_q);
          end else if (step == 5'd14) begin
            feat[F_Q]    <= uq(div_q);
            feat[F_LIFT] <= uq(div_q) - uq(f_q);
          end else if (step <= 5'd18) begin
            if (step == 5'd18) begin
              feat[F_QMAX]  <= uq((div_q > qmax) ? div_q : qmax);
              feat[F_QMIN]  <= uq((div_q < qmin) ? div_q : qmin);
              feat[F_ANISO] <= uq((div_q > qmax) ? div_q : qmax) - uq((div_q < qmin) ? div_q : qmin);
            end
            qmax <= (div_q > qmax) ? div_q : qmax;
            qmin <= (div_q < qmin) ? div_q : qmin;
          end else begin
            feat[F_FFPF] <= uq(div_q);
          end
          // next step
          if (step == 5'd11) begin
            zi <= '0; hi <= '0; lo <= (QB + 1)'(ONE); anyok <= 1'b0; maxf <= '0; maxfz <= '0;
            st <= S_ZONE;
          end else if (step == 5'd14) begin
            qmax <= '0; qmin <= (QB + 1)'(ONE);
            step <= 5'd15;
            st <= S_FEAT_I;
          end else if (step == 5'd19) begin
            st <= S_GBT;
          end else begin
            step <= step + 5'd1;
            st <= S_FEAT_I;
          end
        end
        // zone loop: sector rates (zones with >= TMIN dies), worst-sector shares
        S_ZONE: begin
          if (fz_rd > maxf)  maxf  <= fz_rd;
          if (fz_rd > maxfz) maxfz <= fz_rd;
          st <= (tz_rd >= CW'(TMIN)) ? S_ZONE_W : S_ZONE_END;
        end
        S_ZONE_W: if (div_done) begin
          if (div_q > hi) hi <= div_q;
          if (div_q < lo) lo <= div_q;
          anyok <= 1'b1;
          st <= S_ZONE_END;
        end
        S_ZONE_END: begin
          if (zi[2:0] == 3'd7) begin
            feat[F_SMAX + ring_i] <= anyok ? uq(hi) : '0;
            feat[F_SMIN + ring_i] <= anyok ? uq(lo) : '0;
            st <= S_SCONC_W;
          end else begin
            zi <= zi + 6'd1;
            st <= S_ZONE;
          end
        end
        S_SCONC_W: if (div_done) begin
          feat[F_SCONC + ring_i] <= uq(div_q);
          hi <= '0; lo <= (QB + 1)'(ONE); anyok <= 1'b0; maxf <= '0;
          if (zi == 6'(NZ - 1)) begin
            step <= 5'd13;
            st <= S_FEAT_I;
          end else begin
            zi <= zi + 6'd1;
            st <= S_ZONE;
          end
        end
        // ---------------------------------------------------------- trees, top class
        S_GBT: st <= S_GBT_W;
        S_GBT_W: if (gbt_done) begin
          best   <= $signed(score[0]);
          bi     <= '0;
          second <= {1'b1, {(SCW - 1){1'b0}}};     // most negative score
          tk     <= 4'd1;
          st     <= S_TOP;
        end
        S_TOP: begin
          if ($signed(score[tk]) > best) begin
            second <= best;
            best   <= $signed(score[tk]);
            bi     <= tk;
          end else if ($signed(score[tk]) > second) begin
            second <= $signed(score[tk]);
          end
          if (tk == 4'(NCLS - 1)) st <= S_DECIDE;
          tk <= tk + 4'd1;
        end
        S_DECIDE: begin
          top_r    <= bi;
          margin_r <= margin_c;
          run_cls  <= run_cls_c;
          run_len  <= run_len_c;
          if (!called && ok_c && run_len_c >= persist_eff) begin
            called     <= 1'b1;
            call_cls   <= bi;
            call_ndies <= cnt_t;
          end
          site_i <= '0;
          st <= S_SITE;
        end
        // ---------------------------------------------------------- probe-site check
        S_SITE: begin
          if (site_flag || c_nsites == 4'd0 || site_i >= n_site_eff) st <= S_EMIT;
          else if (site_ok) st <= S_SITE_W1;
          else site_i <= site_i + 4'd1;
        end
        S_SITE_W1: if (div_done) begin
          rs_q <= div_q;
          st <= S_SITE_I2;
        end
        S_SITE_I2: st <= S_SITE_W2;
        S_SITE_W2: if (div_done) begin
          if (uq(rs_q) - uq(div_q) >= c_sitedelta) begin
            site_flag  <= 1'b1;
            site_id    <= site_i[2:0];
            site_ndies <= cnt_t;
            st <= S_EMIT;
          end else begin
            site_i <= site_i + 4'd1;
            st <= S_SITE;
          end
        end
        // ---------------------------------------------------------- record out
        S_EMIT: st <= d_last ? S_DONE : S_WAIT;
        default: st <= S_IDLE;
      endcase
    end
  end

  // checkpoint record (registers; read while ck_valid is high)
  always_comb begin
    ck.ndies      = cnt_t;
    ck.n_dup      = n_dup;
    ck.n_oor      = n_oor;
    ck.final_ck   = d_last;
    ck.feat       = feat;
    ck.score      = score;
    ck.top        = top_r;
    ck.margin     = margin_r;
    ck.run_cls    = run_cls;
    ck.run_len    = run_len;
    ck.called     = called;
    ck.call_cls   = call_cls;
    ck.call_ndies = call_ndies;
    ck.site_flag  = site_flag;
    ck.site_id    = site_id;
    ck.site_ndies = site_ndies;
  end

endmodule
