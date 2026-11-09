// wpe_div.v
// q = a/b in Q.12 for the features:
//   b == 0 -> 0, a >= b -> 4096, else floor(a*4096/b) using 12 restoring steps
// a and b get registered first. Without that the operand muxes in the engine plus the
// compares here were one long path (only 0.08 ns slack at 100 MHz).
// done comes 2 clocks after start for the special cases, 14 clocks otherwise.
module wpe_div (clk, rst, start, a, b, busy, done, q);
`include "wpe_defs.vh"
  input  wire            clk;
  input  wire            rst;
  input  wire            start;     // ignored while busy
  input  wire [DVW-1:0]  a;
  input  wire [DVW-1:0]  b;
  output reg             busy;
  output reg             done;
  output reg  [QB:0]     q;

  reg [DVW-1:0] ra, bd, r;    // r: remainder < divisor
  reg           chk;          // operands latched, check special cases next
  reg [3:0]     n;
  wire [DVW:0]  r2 = {r, 1'b0};

  always @(posedge clk) begin
    done <= 1'b0;
    if (rst) begin
      busy <= 1'b0;
      chk  <= 1'b0;
      q    <= 0;
    end else if (!busy) begin
      if (start) begin
        ra   <= a;
        bd   <= b;
        busy <= 1'b1;
        chk  <= 1'b1;
      end
    end else if (chk) begin
      chk <= 1'b0;
      if (bd == 0) begin
        q    <= 0;
        busy <= 1'b0;
        done <= 1'b1;
      end else if (ra >= bd) begin
        q    <= ONE;
        busy <= 1'b0;
        done <= 1'b1;
      end else begin
        r <= ra;
        q <= 0;
        n <= 0;
      end
    end else begin
      if (r2 >= {1'b0, bd}) begin
        r <= r2 - {1'b0, bd};
        q <= {q[QB-1:0], 1'b1};
      end else begin
        r <= r2[DVW-1:0];
        q <= {q[QB-1:0], 1'b0};
      end
      n <= n + 4'd1;
      if (n == QB - 1) begin
        busy <= 1'b0;
        done <= 1'b1;
      end
    end
  end

endmodule
