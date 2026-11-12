// wpe_gbt.v
// gradient boosted trees: 576 trees of depth 3 (64 rounds x 9 classes), ROMs in BRAM
//   node rom: 7 words per tree {feature[5:0], threshold[13:0]}, heap order
//   leaf rom: 8 leaves per tree, then the 9 start values (16 bit, Q.8)
// tree t adds to class t%9, go right when feature > threshold, 2 clocks per level.
// the .mem files come from training/train.py. Features are read from the engine
// through fidx / fval.
module wpe_gbt (clk, rst, start, busy, done, fidx, fval, score_flat);
`include "wpe_defs.vh"
  parameter NODE_FILE = "wpe_nodes.mem";
  parameter LEAF_FILE = "wpe_leaves.mem";
  input  wire                    clk;
  input  wire                    rst;
  input  wire                    start;
  output wire                    busy;
  output reg                     done;          // pulse, scores valid
  output wire [FIW-1:0]          fidx;
  input  wire signed [FW-1:0]    fval;
  output wire [NCLS*SCW-1:0]     score_flat;    // class k at [k*SCW +: SCW] (signed)

  (* rom_style = "block" *) reg [FIW+FW-1:0] node_rom [0:NODE_WORDS-1];
  (* rom_style = "block" *) reg [LW-1:0]     leaf_rom [0:LEAF_WORDS-1];
  initial begin
    $readmemh(NODE_FILE, node_rom);
    $readmemh(LEAF_FILE, leaf_rom);
  end

  reg  [NAW-1:0]    node_addr;
  reg  [LAW-1:0]    leaf_addr;
  reg  [FIW+FW-1:0] node_q;
  reg  [LW-1:0]     leaf_q;
  always @(posedge clk) begin
    node_q <= node_rom[node_addr];
    leaf_q <= leaf_rom[leaf_addr];
  end

  localparam [2:0] G_IDLE = 3'd0, G_BASE_RD = 3'd1, G_BASE_WR = 3'd2, G_NODE_RD = 3'd3,
                   G_NODE_CMP = 3'd4, G_LEAF_RD = 3'd5, G_LEAF_ACC = 3'd6;
  reg [2:0] gs;

  reg [9:0]     t;          // tree 0..575
  reg [3:0]     cls;        // t mod 9
  reg [NAW-1:0] nbase;      // t * 7
  reg [LAW-1:0] lbase;      // t * 8
  reg [2:0]     node;       // heap index 0..6, leaf index 0..7 in G_LEAF_RD
  reg [1:0]     level;
  reg [3:0]     k;          // start-value index

  reg [SCW-1:0] score [0:NCLS-1];
  genvar gk;
  generate
    for (gk = 0; gk < NCLS; gk = gk + 1) begin : g_score
      assign score_flat[gk*SCW +: SCW] = score[gk];
    end
  endgenerate

  wire signed [FW-1:0] thr_q     = node_q[FW-1:0];
  wire                 go_right  = fval > thr_q;
  wire [3:0]           node_next = {node, 1'b0} + 4'd1 + {3'd0, go_right};
  wire [LW-1:0]        leaf_val  = leaf_q;
  wire [SCW-1:0]       leaf_ext  = {{(SCW-LW){leaf_val[LW-1]}}, leaf_val};   // sign-extend
  assign fidx = node_q[FIW+FW-1:FW];
  assign busy = (gs != G_IDLE);

  // rom addresses
  always @* begin
    node_addr = nbase + node;
    if (gs == G_BASE_RD || gs == G_BASE_WR)
      leaf_addr = NTREE * NLEAF + k;
    else
      leaf_addr = lbase + node;
  end

  always @(posedge clk) begin
    done <= 1'b0;
    if (rst) begin
      gs <= G_IDLE;
    end else begin
      case (gs)
        G_IDLE: if (start) begin
          k  <= 0;
          gs <= G_BASE_RD;
        end
        G_BASE_RD: gs <= G_BASE_WR;                       // leaf_q <= start value k
        G_BASE_WR: begin
          score[k] <= leaf_ext;
          if (k == NCLS - 1) begin
            t <= 0; cls <= 0; nbase <= 0; lbase <= 0; node <= 0; level <= 0;
            gs <= G_NODE_RD;
          end else begin
            k  <= k + 4'd1;
            gs <= G_BASE_RD;
          end
        end
        G_NODE_RD: gs <= G_NODE_CMP;                      // node_q <= node word
        G_NODE_CMP: begin
          level <= level + 2'd1;
          if (level == DEPTH - 1) begin
            node <= node_next - NNODE;                    // heap index 7..14 -> leaf 0..7
            gs   <= G_LEAF_RD;
          end else begin
            node <= node_next[2:0];                       // child 1..6
            gs   <= G_NODE_RD;
          end
        end
        G_LEAF_RD: gs <= G_LEAF_ACC;                      // leaf_q <= leaf value
        G_LEAF_ACC: begin
          score[cls] <= score[cls] + leaf_ext;
          if (t == NTREE - 1) begin
            done <= 1'b1;
            gs   <= G_IDLE;
          end else begin
            t     <= t + 10'd1;
            cls   <= (cls == NCLS - 1) ? 4'd0 : cls + 4'd1;
            nbase <= nbase + NNODE;
            lbase <= lbase + NLEAF;
            node  <= 0;
            level <= 0;
            gs    <= G_NODE_RD;
          end
        end
        default: gs <= G_IDLE;
      endcase
    end
  end

endmodule
