// tb_wpe_engine.v
// File driven testbench. Every line of the list file is "<cfg> <dies> <out>":
//   cfg  - "addr value" per line, written through the config bus
//   dies - "x y fail site last" per line
//   out  - one line per checkpoint record, same field order as CK_KEYS in model/wpe_params.py
// plusargs: +LIST:<file>  +GAP:<0..99, % of clocks with s_valid low>  +SEED:<n>
// (':' not '=', the xsim .bat wrapper splits arguments on '=')
// Runs go back to back without a reset in between. scripts/run_regression.py writes the
// files and compares the outputs with the python model.
`timescale 1ns/1ps

module tb_wpe_engine;
`include "wpe_defs.vh"

  reg clk = 1'b0;
  reg rst = 1'b1;
  always #5 clk = ~clk;     // 100 MHz

  reg                cfg_we    = 1'b0;
  reg  [CAW-1:0]     cfg_addr  = 0;
  reg  [CDW-1:0]     cfg_wdata = 0;
  reg                start     = 1'b0;
  reg                s_valid   = 1'b0;
  wire               s_ready;
  reg  [XYW-1:0]     s_x = 0;
  reg  [XYW-1:0]     s_y = 0;
  reg                s_fail = 1'b0;
  reg  [2:0]         s_site = 0;
  reg                s_last = 1'b0;
  wire               busy, done, ck_valid;
  wire [CK_BITS-1:0] ck_flat;

  wpe_engine dut (
    .clk(clk), .rst(rst), .cfg_we(cfg_we), .cfg_addr(cfg_addr), .cfg_wdata(cfg_wdata),
    .start(start), .s_valid(s_valid), .s_ready(s_ready), .s_x(s_x), .s_y(s_y),
    .s_fail(s_fail), .s_site(s_site), .s_last(s_last), .busy(busy), .done(done),
    .ck_valid(ck_valid), .ck_flat(ck_flat)
  );

  // some record fields with names, easier to read in the wave window
  wire [CW-1:0]  ck_ndies     = ck_flat[CK_NDIES +: CW];
  wire [CLW-1:0] ck_top       = ck_flat[CK_TOP +: CLW];
  wire           ck_called    = ck_flat[CK_CALLED];
  wire [CLW-1:0] ck_call_cls  = ck_flat[CK_CALLCLS +: CLW];
  wire [CW-1:0]  ck_call_n    = ck_flat[CK_CALLN +: CW];
  wire           ck_site_flag = ck_flat[CK_SITEF];
  reg  [8*9-1:0] top_name, call_name;    // ASCII radix in the wave window

  function [8*9-1:0] cls_name;
    input [3:0] c;
    begin
      case (c)
        4'd0:    cls_name = "none";
        4'd1:    cls_name = "Center";
        4'd2:    cls_name = "Donut";
        4'd3:    cls_name = "Edge-Loc";
        4'd4:    cls_name = "Edge-Ring";
        4'd5:    cls_name = "Loc";
        4'd6:    cls_name = "Near-full";
        4'd7:    cls_name = "Random";
        4'd8:    cls_name = "Scratch";
        default: cls_name = "?";
      endcase
    end
  endfunction

  always @* begin
    top_name  = cls_name(ck_top);
    call_name = ck_called ? cls_name(ck_call_cls) : "-";
  end

  always @(posedge ck_called)
    $display("early call: %0s after %0d dies (t = %0t)", call_name, ck_call_n, $time);

  reg fire_q = 1'b0;
  always @(posedge clk) fire_q <= s_valid && s_ready;

  reg [63:0] cycles = 0;
  always @(posedge clk) cycles <= cycles + 1;

  integer    gap_pct = 0;
  integer    nruns = 0;
  integer    nerr = 0;
  integer    out_fd = 0;
  integer    nrec = 0;
  integer    wi;
  reg [31:0] rng = 32'd1;

  // xorshift32 so the gap pattern is the same on any simulator
  task next_rand;
    begin
      rng = rng ^ (rng << 13);
      rng = rng ^ (rng >> 17);
      rng = rng ^ (rng << 5);
    end
  endtask

  // one line per record, sampled on the clock edge where ck_valid is high
  always @(posedge clk) begin
    if (ck_valid && out_fd != 0) begin
      $fwrite(out_fd, "%0d %0d %0d %0d", ck_flat[CK_NDIES +: CW], ck_flat[CK_NDUP +: CW],
              ck_flat[CK_NOOR +: CW], ck_flat[CK_FINAL]);
      for (wi = 0; wi < NF; wi = wi + 1)
        $fwrite(out_fd, " %0d", $signed(ck_flat[CK_FEAT + wi*FW +: FW]));
      for (wi = 0; wi < NCLS; wi = wi + 1)
        $fwrite(out_fd, " %0d", $signed(ck_flat[CK_SCORE + wi*SCW +: SCW]));
      $fwrite(out_fd, " %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",
              ck_flat[CK_TOP +: CLW], ck_flat[CK_MARGIN +: MGW], ck_flat[CK_RUNCLS +: CLW],
              ck_flat[CK_RUNLEN +: 4], ck_flat[CK_CALLED], ck_flat[CK_CALLCLS +: CLW],
              ck_flat[CK_CALLN +: CW], ck_flat[CK_SITEF], ck_flat[CK_SITEID +: 3],
              ck_flat[CK_SITEN +: CW]);
      nrec = nrec + 1;
    end
  end

  task automatic run_one;
    input [8*512-1:0] cfg_f;
    input [8*512-1:0] die_f;
    input [8*512-1:0] out_f;
    integer           cf, df, x, y, fl, si, la, idle;
    reg signed [63:0] a, v;
    reg               have, exhausted, running;
    reg [63:0]        t0, consumed;
    begin
      cf = $fopen(cfg_f, "r");
      df = $fopen(die_f, "r");
      if (cf == 0 || df == 0) begin
        $display("ERROR cannot open %0s or %0s", cfg_f, die_f);
        nerr = nerr + 1;
      end else begin
        // config registers
        while ($fscanf(cf, "%d %d", a, v) == 2) begin
          cfg_we    = 1'b1;
          cfg_addr  = a[CAW-1:0];
          cfg_wdata = v[CDW-1:0];
          @(posedge clk); #1;
        end
        cfg_we = 1'b0;
        $fclose(cf);

        out_fd = $fopen(out_f, "w");
        nrec = 0;
        start = 1'b1;
        @(posedge clk); #1;
        start = 1'b0;
        t0 = cycles;

        have = 0; exhausted = 0; idle = 0; consumed = 0; running = 1;
        while (running) begin
          if (fire_q) begin
            have = 0;
            consumed = consumed + 1;
          end
          if (done) begin
            running = 0;
          end else begin
            if (!have && !exhausted) begin
              if ($fscanf(df, "%d %d %d %d %d", x, y, fl, si, la) == 5) begin
                have   = 1;
                s_x    = x;
                s_y    = y;
                s_fail = fl;
                s_site = si;
                s_last = la;
              end else begin
                exhausted = 1;
              end
            end
            next_rand;
            s_valid = have && ((rng % 100) >= gap_pct);
            // watchdog. Longest normal pause is the map clear (65536 clocks max)
            idle = fire_q ? 0 : idle + 1;
            if (idle > 200000) running = 0;
            else begin
              @(posedge clk); #1;
            end
          end
        end
        s_valid = 1'b0;
        $fclose(df);
        @(posedge clk); #1;
        if (!done) begin
          $display("ERROR no progress for 200000 clocks (dies ran out or engine stuck)");
          nerr = nerr + 1;
          $fwrite(out_fd, "# ERROR stuck\n");
          rst = 1'b1;
          repeat (3) @(posedge clk);
          #1 rst = 1'b0;
        end
        $fwrite(out_fd, "# cycles %0d consumed %0d records %0d\n", cycles - t0, consumed, nrec);
        $fclose(out_fd);
        out_fd = 0;
      end
    end
  endtask

  reg [8*512-1:0] list_f, cfg_f, die_f, out_f;
  integer         lf, seed;
  initial begin
    if (!$value$plusargs("LIST:%s", list_f)) begin
      $display("ERROR missing +LIST:<file>");
      $finish;
    end
    if (!$value$plusargs("GAP:%d", gap_pct)) gap_pct = 0;
    if ($value$plusargs("SEED:%d", seed)) rng = (seed == 0) ? 32'd1 : seed;
    // 200 ns reset, the routed netlist keeps all flops in global reset for 100 ns
    repeat (20) @(posedge clk);
    #1 rst = 1'b0;
    @(posedge clk); #1;
    lf = $fopen(list_f, "r");
    if (lf == 0) begin
      $display("ERROR cannot open list %0s", list_f);
      $finish;
    end
    while ($fscanf(lf, "%s %s %s", cfg_f, die_f, out_f) == 3) begin
      run_one(cfg_f, die_f, out_f);
      nruns = nruns + 1;
      $display("run %0d done", nruns);
    end
    $fclose(lf);
    $display("TB_FINISHED runs=%0d errors=%0d", nruns, nerr);
    $finish;
  end

endmodule
