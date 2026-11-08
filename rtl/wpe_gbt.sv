// wpe_gbt.sv - gradient-boosted tree ensemble, one tree level per two clocks.
//
// ROM images come from scripts/train.py:
//   node ROM: NTREE * 7 words {feature index[5:0], threshold[13:0]} (heap order per tree)
//   leaf ROM: NTREE * 8 leaf values, then NCLS start values (16-bit, Q.8)
// Tree t adds to class t mod 9. At each node the walk goes right when the selected
// feature is greater than the threshold. Scores start from the class start values.
// The feature value is fetched through fidx/fval from the caller's register file.
module wpe_gbt
  import wpe_pkg::*;
#(
  parameter NODE_FILE = "wpe_nodes.mem",
  parameter LEAF_FILE = "wpe_leaves.mem"
)(
  input  logic                         clk,
  input  logic                         rst,
  input  logic                         start,
  output logic                         busy,
  output logic                         done,      // one-clock pulse, scores valid
  output logic [FIW-1:0]               fidx,
  input  logic signed [FW-1:0]         fval,
  output logic signed [NCLS-1:0][SCW-1:0] score
);
  localparam int NAW = $clog2(NODE_WORDS);
  localparam int LAW = $clog2(LEAF_WORDS);

  (* rom_style = "block" *) logic [FIW+FW-1:0] node_rom [0:NODE_WORDS-1];
  (* rom_style = "block" *) logic [LW-1:0]     leaf_rom [0:LEAF_WORDS-1];
  initial begin
    $readmemh(NODE_FILE, node_rom);
    $readmemh(LEAF_FILE, leaf_rom);
  end

  logic [NAW-1:0]    node_addr;
  logic [LAW-1:0]    leaf_addr;
  logic [FIW+FW-1:0] node_q;
  logic [LW-1:0]     leaf_q;
  always_ff @(posedge clk) begin
    node_q <= node_rom[node_addr];
    leaf_q <= leaf_rom[leaf_addr];
  end

  typedef enum logic [2:0] {G_IDLE, G_BASE_RD, G_BASE_WR, G_NODE_RD, G_NODE_CMP, G_LEAF_RD, G_LEAF_ACC} gstate_t;
  gstate_t gs;

  logic [9:0]     t;          // tree 0..575
  logic [3:0]     cls;        // t mod 9
  logic [NAW-1:0] nbase;      // t * 7
  logic [LAW-1:0] lbase;      // t * 8
  logic [2:0]     node;       // heap index 0..6
  logic [1:0]     level;
  logic [3:0]     k;          // start-value index

  logic signed [FW-1:0] thr_q;
  logic                 go_right;
  logic [3:0]           node_next;
  assign fidx      = node_q[FIW+FW-1:FW];
  assign thr_q     = $signed(node_q[FW-1:0]);
  assign go_right  = fval > thr_q;
  assign node_next = {node, 1'b0} + 4'd1 + {3'd0, go_right};

  // ROM addresses from registered state
  always_comb begin
    node_addr = nbase + NAW'(node);
    // in G_LEAF_RD `node` holds the leaf index 0..7
    leaf_addr = (gs == G_BASE_RD || gs == G_BASE_WR) ? LAW'(NTREE * NLEAF) + LAW'(k)
                                                     : lbase + LAW'(node);
  end

  assign busy = (gs != G_IDLE);

  always_ff @(posedge clk) begin
    done <= 1'b0;
    if (rst) begin
      gs <= G_IDLE;
    end else begin
      case (gs)
        G_IDLE: if (start) begin
          k  <= '0;
          gs <= G_BASE_RD;
        end
        G_BASE_RD: gs <= G_BASE_WR;                       // leaf_q <= base[k]
        G_BASE_WR: begin
          score[k] <= SCW'($signed(leaf_q));
          if (k == 4'(NCLS - 1)) begin
            t <= '0; cls <= '0; nbase <= '0; lbase <= '0; node <= '0; level <= '0;
            gs <= G_NODE_RD;
          end else begin
            k  <= k + 4'd1;
            gs <= G_BASE_RD;
          end
        end
        G_NODE_RD: gs <= G_NODE_CMP;                      // node_q <= node word
        G_NODE_CMP: begin
          level <= level + 2'd1;
          if (level == 2'(DEPTH - 1)) begin
            node <= 3'(node_next - 4'(NNODE));            // heap index 7..14 -> leaf 0..7
            gs   <= G_LEAF_RD;
          end else begin
            node <= node_next[2:0];                       // child 1..6
            gs   <= G_NODE_RD;
          end
        end
        G_LEAF_RD: gs <= G_LEAF_ACC;                      // leaf_q <= leaf value
        G_LEAF_ACC: begin
          score[cls] <= score[cls] + SCW'($signed(leaf_q));
          if (t == 10'(NTREE - 1)) begin
            done <= 1'b1;
            gs   <= G_IDLE;
          end else begin
            t     <= t + 10'd1;
            cls   <= (cls == 4'(NCLS - 1)) ? 4'd0 : cls + 4'd1;
            nbase <= nbase + NAW'(NNODE);
            lbase <= lbase + LAW'(NLEAF);
            node  <= '0;
            level <= '0;
            gs    <= G_NODE_RD;
          end
        end
        default: gs <= G_IDLE;
      endcase
    end
  end

endmodule
