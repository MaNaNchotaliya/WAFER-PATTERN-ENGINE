// wpe_zone.sv - ring and sector of a die position.
//
// u = (2x - cx2) * wx and v = (2y - cy2) * wy put the elliptical die grid on a circle;
// the ring is the number of thresholds that u^2 + v^2 exceeds, the sector the octant of
// (u, -v). Four register stages: the outputs are valid 4 clocks after x / y change, and
// the caller holds x / y steady for longer than that.
module wpe_zone
  import wpe_pkg::*;
(
  input  logic                  clk,
  input  logic [XYW-1:0]        x,
  input  logic [XYW-1:0]        y,
  input  logic [DW-1:0]         cx2,
  input  logic [DW-1:0]         cy2,
  input  logic [7:0]            wx,
  input  logic [7:0]            wy,
  input  logic [3:0][THW-1:0]   thr,
  output logic [2:0]            ring,
  output logic [2:0]            sect
);
  // stage 1: doubled offsets from the centre, -510..510
  logic signed [10:0] dx2, dy2;
  always_ff @(posedge clk) begin
    dx2 <= $signed({2'b00, x, 1'b0}) - $signed({2'b00, cx2});
    dy2 <= $signed({2'b00, y, 1'b0}) - $signed({2'b00, cy2});
  end

  // stage 2: scaled coordinates, |u|, |v| <= 510 * 255 = 130,050
  logic signed [18:0] u, v;
  always_ff @(posedge clk) begin
    u <= dx2 * $signed({1'b0, wx});
    v <= dy2 * $signed({1'b0, wy});
  end

  // stage 3: squares of the magnitudes (17 x 17 bits) and the octant
  logic [16:0] au, av;
  assign au = u[18] ? 17'(-u) : u[16:0];
  assign av = v[18] ? 17'(-v) : v[16:0];

  logic [33:0] uu, vv;
  logic [2:0]  sect_s3;
  logic [1:0]  q;
  logic        lead;
  always_comb begin
    // (a, b) = (u, -v): b > 0 points towards row 0
    if (u > 0 && v <= 0)       q = 2'd0;
    else if (u <= 0 && v < 0)  q = 2'd1;
    else if (u < 0 && v >= 0)  q = 2'd2;
    else                       q = 2'd3;
    lead = q[0] ? (av > au) : (au > av);
  end
  always_ff @(posedge clk) begin
    uu      <= au * au;
    vv      <= av * av;
    sect_s3 <= {q, ~lead};
  end

  // stage 4: squared radius against the four ring thresholds
  logic [THW-1:0] d2;
  assign d2 = THW'(uu) + THW'(vv);
  always_ff @(posedge clk) begin
    ring <= 3'(d2 > thr[0]) + 3'(d2 > thr[1]) + 3'(d2 > thr[2]) + 3'(d2 > thr[3]);
    sect <= sect_s3;
  end

endmodule
