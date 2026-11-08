// wpe_div.sv - Q.12 ratio for the feature unit.
//   q = 0 if b = 0;  q = 4096 if a >= b;  else floor(a * 4096 / b)
// The operands are registered first (so the caller's operand multiplexers and this unit's
// comparators are in different clock cycles), then either a special case finishes, or
// 12 restoring steps run on the remainder (a < b, so the quotient has 12 bits).
// `done` pulses with the result: 2 clocks after `start` for the special cases, 14 otherwise.
module wpe_div
  import wpe_pkg::*;
(
  input  logic            clk,
  input  logic            rst,
  input  logic            start,     // ignored while busy
  input  logic [DVW-1:0]  a,
  input  logic [DVW-1:0]  b,
  output logic            busy,
  output logic            done,
  output logic [QB:0]     q
);
  logic [DVW-1:0] ra, bd, r;   // r: remainder < divisor
  logic           chk;         // operands latched, decide next clock
  logic [3:0]     n;
  logic [DVW:0]   r2;
  assign r2 = {r, 1'b0};

  always_ff @(posedge clk) begin
    done <= 1'b0;
    if (rst) begin
      busy <= 1'b0;
      chk  <= 1'b0;
      q    <= '0;
    end else if (!busy) begin
      if (start) begin
        ra   <= a;
        bd   <= b;
        busy <= 1'b1;
        chk  <= 1'b1;
      end
    end else if (chk) begin
      chk <= 1'b0;
      if (bd == '0) begin
        q    <= '0;
        busy <= 1'b0;
        done <= 1'b1;
      end else if (ra >= bd) begin
        q    <= (QB + 1)'(ONE);
        busy <= 1'b0;
        done <= 1'b1;
      end else begin
        r <= ra;
        q <= '0;
        n <= '0;
      end
    end else begin
      if (r2 >= {1'b0, bd}) begin
        r <= DVW'(r2 - {1'b0, bd});
        q <= {q[QB-1:0], 1'b1};
      end else begin
        r <= r2[DVW-1:0];
        q <= {q[QB-1:0], 1'b0};
      end
      n <= n + 4'd1;
      if (n == 4'(QB - 1)) begin
        busy <= 1'b0;
        done <= 1'b1;
      end
    end
  end

endmodule
