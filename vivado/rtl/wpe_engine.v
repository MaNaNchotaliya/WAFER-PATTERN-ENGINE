// wpe_engine.v - top level
//
// Wafer map pattern engine. Dies come in prober order (x, y, fail, probe site).
// Per die: drop it if it is outside the map or already tested, read its 8 neighbours
// from the die map and count fail-fail / fail-pass pairs per direction, then update
// the zone (5 rings x 8 sectors), ring, site and wafer counters.
// Every chk dies (and after the last die) there is a checkpoint: 39 features through
// one divider -> 576 trees -> early call rule -> probe site check, and one record goes
// out on ck_flat (layout in wpe_defs.vh).
// Reference model is model/wpe_model.py, vivado/run_regression.py checks against it.
module wpe_engine (clk, rst, cfg_we, cfg_addr, cfg_wdata, start,
                   s_valid, s_ready, s_x, s_y, s_fail, s_site, s_last,
                   busy, done, ck_valid, ck_flat);
`include "wpe_defs.vh"
  parameter NODE_FILE = "wpe_nodes.mem";
  parameter LEAF_FILE = "wpe_leaves.mem";

  input  wire              clk;
  input  wire              rst;          // synchronous, active high
  // config bus
  input  wire              cfg_we;
  input  wire [CAW-1:0]    cfg_addr;
  input  wire [CDW-1:0]    cfg_wdata;
  input  wire              start;        // pulse, starts a new wafer
  // die stream
  input  wire              s_valid;
  output wire              s_ready;
  input  wire [XYW-1:0]    s_x;
  input  wire [XYW-1:0]    s_y;
  input  wire              s_fail;
  input  wire [2:0]        s_site;
  input  wire              s_last;
  // status / records
  output wire              busy;
  output wire              done;
  output wire              ck_valid;
  output wire [CK_BITS-1:0] ck_flat;

  integer k;

  // config registers, written before start
  reg [DW-1:0]          c_w, c_h, c_cx2, c_cy2;
  reg [7:0]             c_wx, c_wy;
  reg [THW-1:0]         c_thr [0:3];
  reg [CW-1:0]          c_ntot;
  reg [CW-1:0]          c_nr [0:NR-1];
  reg [7:0]             c_chk;
  reg [3:0]             c_persist;
  reg [QB:0]            c_minfrac;
  reg [15:0]            c_margin [0:NCLS-1];
  reg [3:0]             c_nsites;
  reg [CW-1:0]          c_sitemin;
  reg signed [FW-1:0]   c_sitedelta;

  always @(posedge clk) begin
    if (rst) begin
      c_w <= 0; c_h <= 0; c_cx2 <= 0; c_cy2 <= 0; c_wx <= 0; c_wy <= 0; c_ntot <= 0;
      c_chk <= 0; c_persist <= 0; c_minfrac <= 0; c_nsites <= 0; c_sitemin <= 0; c_sitedelta <= 0;
      for (k = 0; k < 4; k = k + 1)    c_thr[k]    <= 0;
      for (k = 0; k < NR; k = k + 1)   c_nr[k]     <= 0;
      for (k = 0; k < NCLS; k = k + 1) c_margin[k] <= 0;
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
        R_SITEDELTA: c_sitedelta <= cfg_wdata[FW-1:0];
        default: begin
          for (k = 0; k < 4; k = k + 1)
            if (cfg_addr == R_THR0 + k) c_thr[k] <= cfg_wdata[THW-1:0];
          for (k = 0; k < NR; k = k + 1)
            if (cfg_addr == R_NR0 + k) c_nr[k] <= cfg_wdata[CW-1:0];
          for (k = 0; k < NCLS; k = k + 1)
            if (cfg_addr == R_MARGIN0 + k) c_margin[k] <= cfg_wdata[15:0];
        end
      endcase
    end
  end

  // fsm states
  localparam [4:0] S_IDLE = 5'd0, S_CLEAR = 5'd1, S_WAIT = 5'd2, S_CHECK = 5'd3, S_SCAN = 5'd4,
                   S_UPDATE = 5'd5, S_POST = 5'd6, S_FEAT_I = 5'd7, S_FEAT_W = 5'd8, S_ZONE = 5'd9,
                   S_ZONE_W = 5'd10, S_ZONE_END = 5'd11, S_SCONC_W = 5'd12, S_GBT = 5'd13,
                   S_GBT_W = 5'd14, S_TOP = 5'd15, S_DECIDE = 5'd16, S_SITE = 5'd17,
                   S_SITE_W1 = 5'd18, S_SITE_I2 = 5'd19, S_SITE_W2 = 5'd20, S_EMIT = 5'd21,
                   S_DONE = 5'd22;
  reg [4:0] st;

  // current die
  reg [XYW-1:0] d_x, d_y;
  reg           d_fail, d_last;
  reg [2:0]     d_site;
  reg           periodic;

  // counters
  reg [CW-1:0]  cnt_t, cnt_f, n_dup, n_oor;
  reg [CW-1:0]  rt  [0:NR-1];          // per ring: tested
  reg [CW-1:0]  rf  [0:NR-1];          // per ring: failed
  reg [CW-1:0]  pff [0:3];             // per direction: fail-fail pairs
  reg [CW-1:0]  pxx [0:3];             // per direction: fail-pass pairs
  reg [CW-1:0]  sit [0:NSITE-1];       // per site: tested
  reg [CW-1:0]  sif [0:NSITE-1];       // per site: failed
  reg [7:0]     ck_cnt;

  // decision state
  reg signed [SCW-1:0] best, second;
  reg [CLW-1:0]        bi, tk;
  reg [CLW-1:0]        top_r;
  reg [MGW-1:0]        margin_r;
  reg [CLW-1:0]        run_cls;
  reg [3:0]            run_len;
  reg                  called;
  reg [CLW-1:0]        call_cls;
  reg [CW-1:0]         call_ndies;
  reg                  site_flag;
  reg [2:0]            site_id;
  reg [CW-1:0]         site_ndies;
  reg [QB:0]           rs_q;
  reg [3:0]            site_i;         // probe site being checked

  // die map, 2 bits per die {tested, fail}
  (* ram_style = "block" *) reg [1:0] map_mem [0:65535];
  reg  [2*XYW-1:0] map_addr;
  reg              map_we;
  reg  [1:0]       map_wd;
  reg  [1:0]       map_q;
  always @(posedge clk) begin
    if (map_we) map_mem[map_addr] <= map_wd;
    map_q <= map_mem[map_addr];
  end

  // zone counters, small enough for LUTRAM
  reg  [CW-1:0] tz_mem [0:NZ-1];
  reg  [CW-1:0] fz_mem [0:NZ-1];
  reg  [5:0]    zw_addr, zr_addr;
  reg           zw_en;
  reg  [CW-1:0] tz_wd, fz_wd;
  wire [CW-1:0] tz_rd = tz_mem[zr_addr];
  wire [CW-1:0] fz_rd = fz_mem[zr_addr];
  always @(posedge clk) begin
    if (zw_en) begin
      tz_mem[zw_addr] <= tz_wd;
      fz_mem[zw_addr] <= fz_wd;
    end
  end

  // ring / sector of the current die
  wire [2:0] z_ring, z_sect;
  wpe_zone u_zone (
    .clk(clk), .x(d_x), .y(d_y), .cx2(c_cx2), .cy2(c_cy2), .wx(c_wx), .wy(c_wy),
    .thr_flat({c_thr[3], c_thr[2], c_thr[1], c_thr[0]}), .ring(z_ring), .sect(z_sect)
  );

  // neighbour scan: j=0 is the die itself (retest check), j=1..8 are the neighbours
  // dir 0 horizontal, 1 vertical, 2 is the \ diagonal, 3 is the / diagonal
  reg [3:0]        scan_j;              // position read this clock
  reg [3:0]        pend_j;              // position that map_q belongs to
  reg              pend_inb;
  reg [1:0]        pend_dir;
  reg signed [1:0] ox, oy;
  reg [1:0]        odir;
  always @* begin
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
  wire signed [XYW+1:0] nx = $signed({2'b00, d_x}) + ox;
  wire signed [XYW+1:0] ny = $signed({2'b00, d_y}) + oy;
  wire n_inb = (nx >= 0) && (nx < $signed({1'b0, c_w})) && (ny >= 0) && (ny < $signed({1'b0, c_h}));

  // clear pointers
  reg [XYW-1:0] clr_x, clr_y;
  reg [5:0]     clr_z;
  reg           clr_map_done, clr_z_done;

  always @* begin
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

  // features, every ratio goes through the one divider
  reg              div_start;
  wire             div_busy, div_done;
  reg  [DVW-1:0]   div_a, div_b;
  wire [QB:0]      div_q;
  wpe_div u_div (.clk(clk), .rst(rst), .start(div_start), .a(div_a), .b(div_b),
                 .busy(div_busy), .done(div_done), .q(div_q));

  reg  [FW-1:0]  feat [0:NF-1];        // signed
  reg  [4:0]     step;                 // which feature (12 = zone loop)
  reg  [5:0]     zi;                   // zone index in the loop
  reg  [QB:0]    f_q;                  // wafer fail rate, needed for dr and lift
  reg  [QB:0]    hi, lo, qmax, qmin;
  reg            anyok;
  reg  [CW-1:0]  maxf, maxfz;
  reg  [DVW-1:0] fft, xt;              // pair totals for this checkpoint
  wire [2:0]     ring_i = zi[5:3];

  // Q.12 rate -> signed feature
  function [FW-1:0] uq;
    input [QB:0] v;
    begin
      uq = {{(FW - QB - 1){1'b0}}, v};
    end
  endfunction

  wire [QB:0] qmax_n = (div_q > qmax) ? div_q : qmax;
  wire [QB:0] qmin_n = (div_q < qmin) ? div_q : qmin;

  // divider inputs for each step
  always @* begin
    div_a = 0;
    div_b = 0;
    case (st)
      S_FEAT_I: begin
        if (step == 5'd0) begin
          div_a = cnt_t;  div_b = c_ntot;
        end else if (step == 5'd1) begin
          div_a = cnt_f;  div_b = cnt_t;
        end else if (step <= 5'd6) begin
          div_a = rf[step - 5'd2];  div_b = rt[step - 5'd2];
        end else if (step <= 5'd11) begin
          div_a = rt[step - 5'd7];  div_b = c_nr[step - 5'd7];
        end else if (step == 5'd13) begin
          div_a = maxfz;  div_b = cnt_f;
        end else if (step == 5'd14) begin
          div_a = {fft, 1'b0};  div_b = {fft, 1'b0} + xt;
        end else if (step <= 5'd18) begin
          div_a = {pff[step - 5'd15], 1'b0};
          div_b = {pff[step - 5'd15], 1'b0} + pxx[step - 5'd15];
        end else begin
          div_a = fft;  div_b = {cnt_f, 2'b00};
        end
      end
      S_ZONE:     begin div_a = fz_rd; div_b = tz_rd; end
      S_ZONE_END: begin div_a = maxf;  div_b = rf[ring_i]; end
      S_SITE:     begin div_a = sif[site_i[2:0]]; div_b = sit[site_i[2:0]]; end
      S_SITE_I2:  begin div_a = cnt_f - sif[site_i[2:0]]; div_b = cnt_t - sit[site_i[2:0]]; end
      default: ;
    endcase
  end

  wire [3:0] n_site_eff = (c_nsites > NSITE) ? NSITE : c_nsites;
  wire       site_ok    = (sit[site_i[2:0]] >= c_sitemin) && (cnt_t > sit[site_i[2:0]]);

  always @* begin
    div_start = 1'b0;
    case (st)
      S_FEAT_I:   div_start = 1'b1;
      S_ZONE:     div_start = (tz_rd >= TMIN);
      S_ZONE_END: div_start = (zi[2:0] == 3'd7);
      S_SITE:     div_start = !site_flag && (c_nsites != 4'd0) && (site_i < n_site_eff) && site_ok;
      S_SITE_I2:  div_start = 1'b1;
      default: ;
    endcase
  end

  // tree ensemble
  wire                    gbt_busy, gbt_done;
  wire [FIW-1:0]          gbt_fidx;
  wire [NCLS*SCW-1:0]     score_flat;
  wire                    gbt_start = (st == S_GBT);
  wire signed [FW-1:0]    gbt_fval  = (gbt_fidx < NF) ? feat[gbt_fidx] : {FW{1'b0}};
  wpe_gbt #(.NODE_FILE(NODE_FILE), .LEAF_FILE(LEAF_FILE)) u_gbt (
    .clk(clk), .rst(rst), .start(gbt_start), .busy(gbt_busy), .done(gbt_done),
    .fidx(gbt_fidx), .fval(gbt_fval), .score_flat(score_flat)
  );
  wire signed [SCW-1:0] score_tk = score_flat[tk*SCW +: SCW];
  wire signed [SCW-1:0] score_0  = score_flat[0 +: SCW];

  // early call rule
  wire signed [MGW-1:0] margin_c    = best - second;
  wire [3:0]            persist_eff = (c_persist == 4'd0) ? 4'd1 : c_persist;
  wire signed [FW-1:0]  frac_s      = feat[F_FRAC];
  wire [15:0]           margin_bi   = c_margin[bi];
  wire ok_c = (bi != 0) && (margin_c >= $signed({5'b00000, margin_bi}))
              && (frac_s >= $signed({1'b0, c_minfrac}));
  reg  [3:0]     run_len_c;
  reg  [CLW-1:0] run_cls_c;
  always @* begin
    run_cls_c = run_cls;
    run_len_c = 4'd0;
    if (ok_c) begin
      if (run_len != 4'd0 && bi == run_cls)
        run_len_c = (run_len == RUN_MAX) ? run_len : run_len + 4'd1;
      else begin
        run_cls_c = bi;
        run_len_c = 4'd1;
      end
    end
  end

  // main fsm
  assign s_ready  = (st == S_WAIT);
  assign busy     = (st != S_IDLE) && (st != S_DONE);
  assign done     = (st == S_DONE);
  assign ck_valid = (st == S_EMIT);

  always @* begin
    zr_addr = (st == S_UPDATE) ? {z_ring, z_sect} : zi;
    zw_en   = 1'b0;
    zw_addr = {z_ring, z_sect};
    tz_wd   = tz_rd + 1'b1;
    fz_wd   = fz_rd + d_fail;
    if (st == S_CLEAR) begin
      zw_en   = !clr_z_done;
      zw_addr = clr_z;
      tz_wd   = 0;
      fz_wd   = 0;
    end else if (st == S_UPDATE) begin
      zw_en = 1'b1;
    end
  end

  always @(posedge clk) begin
    if (rst) begin
      st <= S_IDLE;
    end else begin
      case (st)
        // new wafer
        S_IDLE, S_DONE: if (start) begin
          cnt_t <= 0; cnt_f <= 0; n_dup <= 0; n_oor <= 0; ck_cnt <= 0;
          for (k = 0; k < NR; k = k + 1)    begin rt[k]  <= 0; rf[k]  <= 0; end
          for (k = 0; k < 4; k = k + 1)     begin pff[k] <= 0; pxx[k] <= 0; end
          for (k = 0; k < NSITE; k = k + 1) begin sit[k] <= 0; sif[k] <= 0; end
          run_cls <= 0; run_len <= 0; called <= 1'b0; call_cls <= 0; call_ndies <= 0;
          site_flag <= 1'b0; site_id <= 0; site_ndies <= 0; top_r <= 0; margin_r <= 0;
          d_last <= 1'b0;
          clr_x <= 0; clr_y <= 0; clr_z <= 0;
          clr_map_done <= (c_w == 0) || (c_h == 0);
          clr_z_done <= 1'b0;
          st <= S_CLEAR;
        end
        S_CLEAR: begin
          if (!clr_map_done) begin
            if ({1'b0, clr_x} == c_w - 1'b1) begin
              clr_x <= 0;
              if ({1'b0, clr_y} == c_h - 1'b1) clr_map_done <= 1'b1;
              else clr_y <= clr_y + 1'b1;
            end else begin
              clr_x <= clr_x + 1'b1;
            end
          end
          if (!clr_z_done) begin
            if (clr_z == NZ - 1) clr_z_done <= 1'b1;
            else clr_z <= clr_z + 6'd1;
          end
          if (clr_map_done && clr_z_done) st <= S_WAIT;
        end
        // one die
        S_WAIT: if (s_valid) begin
          d_x <= s_x; d_y <= s_y; d_fail <= s_fail; d_site <= s_site; d_last <= s_last;
          periodic <= 1'b0;
          st <= S_CHECK;
        end
        S_CHECK: begin
          if ({1'b0, d_x} >= c_w || {1'b0, d_y} >= c_h) begin
            n_oor <= n_oor + 1'b1;
            st <= S_POST;
          end else begin
            pend_j <= 0;               // own position is read this clock
            scan_j <= 4'd1;
            st <= S_SCAN;
          end
        end
        S_SCAN: begin
          // map_q has the data for pend_j, scan_j is read now
          if (pend_j == 4'd0 && map_q[1]) begin
            n_dup <= n_dup + 1'b1;     // retest, ignore it
            st <= S_POST;
          end else begin
            if (pend_j != 4'd0 && pend_inb && map_q[1]) begin
              if (d_fail && map_q[0])       pff[pend_dir] <= pff[pend_dir] + 1'b1;
              else if (d_fail != map_q[0])  pxx[pend_dir] <= pxx[pend_dir] + 1'b1;
            end
            pend_j   <= scan_j;
            pend_inb <= n_inb;
            pend_dir <= odir;
            scan_j   <= scan_j + 4'd1;
            if (pend_j == 4'd8) st <= S_UPDATE;
          end
        end
        S_UPDATE: begin
          // map write and zone counter update are in the comb blocks above
          rt[z_ring] <= rt[z_ring] + 1'b1;
          rf[z_ring] <= rf[z_ring] + d_fail;
          cnt_t <= cnt_t + 1'b1;
          cnt_f <= cnt_f + d_fail;
          sit[d_site] <= sit[d_site] + 1'b1;
          sif[d_site] <= sif[d_site] + d_fail;
          if (c_chk != 8'd0) begin
            if (ck_cnt + 8'd1 == c_chk) begin
              ck_cnt   <= 0;
              periodic <= 1'b1;
            end else begin
              ck_cnt <= ck_cnt + 8'd1;
            end
          end
          st <= S_POST;
        end
        S_POST: begin
          if (periodic || d_last) begin
            fft  <= pff[0] + pff[1] + pff[2] + pff[3];
            xt   <= pxx[0] + pxx[1] + pxx[2] + pxx[3];
            step <= 0;
            st   <= S_FEAT_I;
          end else begin
            st <= S_WAIT;
          end
        end
        // checkpoint: features
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
              feat[F_QMAX]  <= uq(qmax_n);
              feat[F_QMIN]  <= uq(qmin_n);
              feat[F_ANISO] <= uq(qmax_n) - uq(qmin_n);
            end
            qmax <= qmax_n;
            qmin <= qmin_n;
          end else begin
            feat[F_FFPF] <= uq(div_q);
          end
          // next step
          if (step == 5'd11) begin
            zi <= 0; hi <= 0; lo <= ONE; anyok <= 1'b0; maxf <= 0; maxfz <= 0;
            st <= S_ZONE;
          end else if (step == 5'd14) begin
            qmax <= 0; qmin <= ONE;
            step <= 5'd15;
            st <= S_FEAT_I;
          end else if (step == 5'd19) begin
            st <= S_GBT;
          end else begin
            step <= step + 5'd1;
            st <= S_FEAT_I;
          end
        end
        // zone loop: sector rates (zones with >= TMIN dies) and worst sector share
        S_ZONE: begin
          if (fz_rd > maxf)  maxf  <= fz_rd;
          if (fz_rd > maxfz) maxfz <= fz_rd;
          st <= (tz_rd >= TMIN) ? S_ZONE_W : S_ZONE_END;
        end
        S_ZONE_W: if (div_done) begin
          if (div_q > hi) hi <= div_q;
          if (div_q < lo) lo <= div_q;
          anyok <= 1'b1;
          st <= S_ZONE_END;
        end
        S_ZONE_END: begin
          if (zi[2:0] == 3'd7) begin
            feat[F_SMAX + ring_i] <= anyok ? uq(hi) : {FW{1'b0}};
            feat[F_SMIN + ring_i] <= anyok ? uq(lo) : {FW{1'b0}};
            st <= S_SCONC_W;
          end else begin
            zi <= zi + 6'd1;
            st <= S_ZONE;
          end
        end
        S_SCONC_W: if (div_done) begin
          feat[F_SCONC + ring_i] <= uq(div_q);
          hi <= 0; lo <= ONE; anyok <= 1'b0; maxf <= 0;
          if (zi == NZ - 1) begin
            step <= 5'd13;
            st <= S_FEAT_I;
          end else begin
            zi <= zi + 6'd1;
            st <= S_ZONE;
          end
        end
        // trees, then best and second best class
        S_GBT: st <= S_GBT_W;
        S_GBT_W: if (gbt_done) begin
          best   <= score_0;
          bi     <= 0;
          second <= {1'b1, {(SCW - 1){1'b0}}};     // most negative score
          tk     <= 4'd1;
          st     <= S_TOP;
        end
        S_TOP: begin
          if (score_tk > best) begin
            second <= best;
            best   <= score_tk;
            bi     <= tk;
          end else if (score_tk > second) begin
            second <= score_tk;
          end
          if (tk == NCLS - 1) st <= S_DECIDE;
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
          site_i <= 0;
          st <= S_SITE;
        end
        // probe site check
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
          if ($signed(uq(rs_q) - uq(div_q)) >= c_sitedelta) begin
            site_flag  <= 1'b1;
            site_id    <= site_i[2:0];
            site_ndies <= cnt_t;
            st <= S_EMIT;
          end else begin
            site_i <= site_i + 4'd1;
            st <= S_SITE;
          end
        end
        // record out
        S_EMIT: st <= d_last ? S_DONE : S_WAIT;
        default: st <= S_IDLE;
      endcase
    end
  end

  // checkpoint record
  assign ck_flat[CK_NDIES  +: CW]        = cnt_t;
  assign ck_flat[CK_NDUP   +: CW]        = n_dup;
  assign ck_flat[CK_NOOR   +: CW]        = n_oor;
  assign ck_flat[CK_FINAL]               = d_last;
  genvar gf;
  generate
    for (gf = 0; gf < NF; gf = gf + 1) begin : g_feat
      assign ck_flat[CK_FEAT + gf*FW +: FW] = feat[gf];
    end
  endgenerate
  assign ck_flat[CK_SCORE  +: NCLS*SCW]  = score_flat;
  assign ck_flat[CK_TOP    +: CLW]       = top_r;
  assign ck_flat[CK_MARGIN +: MGW]       = margin_r;
  assign ck_flat[CK_RUNCLS +: CLW]       = run_cls;
  assign ck_flat[CK_RUNLEN +: 4]         = run_len;
  assign ck_flat[CK_CALLED]              = called;
  assign ck_flat[CK_CALLCLS +: CLW]      = call_cls;
  assign ck_flat[CK_CALLN  +: CW]        = call_ndies;
  assign ck_flat[CK_SITEF]               = site_flag;
  assign ck_flat[CK_SITEID +: 3]         = site_id;
  assign ck_flat[CK_SITEN  +: CW]        = site_ndies;

endmodule
