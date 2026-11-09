// wpe_zone.v
// ring (0..4) and sector (0..7) of a die.
// u = (2x-cx2)*wx, v = (2y-cy2)*wy makes the die grid round (dies are not square),
// ring = how many of the 4 thresholds u^2+v^2 is above, sector = octant of (u,-v).
// 4 pipeline stages. The engine holds x/y for longer than that so no valid signal needed.
module wpe_zone (clk, x, y, cx2, cy2, wx, wy, thr_flat, ring, sect);
`include "wpe_defs.vh"
  input  wire              clk;
  input  wire [XYW-1:0]    x;
  input  wire [XYW-1:0]    y;
  input  wire [DW-1:0]     cx2;
  input  wire [DW-1:0]     cy2;
  input  wire [7:0]        wx;
  input  wire [7:0]        wy;
  input  wire [4*THW-1:0]  thr_flat;     // threshold k at [k*THW +: THW]
  output reg  [2:0]        ring;
  output reg  [2:0]        sect;

  // stage 1: offset from centre (x2), -510..510
  reg signed [10:0] dx2, dy2;
  always @(posedge clk) begin
    dx2 <= $signed({2'b00, x, 1'b0}) - $signed({2'b00, cx2});
    dy2 <= $signed({2'b00, y, 1'b0}) - $signed({2'b00, cy2});
  end

  // stage 2: scale, |u|,|v| <= 510*255 = 130050
  reg signed [18:0] u, v;
  always @(posedge clk) begin
    u <= dx2 * $signed({1'b0, wx});
    v <= dy2 * $signed({1'b0, wy});
  end

  // stage 3: squares (17x17) and octant
  wire [18:0] u_neg = -u;
  wire [18:0] v_neg = -v;
  wire [16:0] au = u[18] ? u_neg[16:0] : u[16:0];
  wire [16:0] av = v[18] ? v_neg[16:0] : v[16:0];

  reg [1:0] q;
  reg       lead;
  always @* begin
    // octant of (u, -v); -v > 0 is towards row 0
    if (u > 0 && v <= 0)       q = 2'd0;
    else if (u <= 0 && v < 0)  q = 2'd1;
    else if (u < 0 && v >= 0)  q = 2'd2;
    else                       q = 2'd3;
    lead = q[0] ? (av > au) : (au > av);
  end

  reg [33:0] uu, vv;
  reg [2:0]  sect_s3;
  always @(posedge clk) begin
    uu      <= au * au;
    vv      <= av * av;
    sect_s3 <= {q, ~lead};
  end

  // stage 4: compare r^2 with the ring thresholds
  wire [THW-1:0] d2 = {2'b00, uu} + {2'b00, vv};
  wire [2:0] gt0 = (d2 > thr_flat[0*THW +: THW]) ? 3'd1 : 3'd0;
  wire [2:0] gt1 = (d2 > thr_flat[1*THW +: THW]) ? 3'd1 : 3'd0;
  wire [2:0] gt2 = (d2 > thr_flat[2*THW +: THW]) ? 3'd1 : 3'd0;
  wire [2:0] gt3 = (d2 > thr_flat[3*THW +: THW]) ? 3'd1 : 3'd0;
  always @(posedge clk) begin
    ring <= gt0 + gt1 + gt2 + gt3;
    sect <= sect_s3;
  end

endmodule
