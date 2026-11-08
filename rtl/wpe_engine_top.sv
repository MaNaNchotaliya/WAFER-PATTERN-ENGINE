// wpe_engine_top.sv - flat-port wrapper (synthesis top; the testbench drives it for both
// RTL and routed-netlist simulation).
module wpe_engine_top
  import wpe_pkg::*;
#(
  parameter NODE_FILE = "wpe_nodes.mem",
  parameter LEAF_FILE = "wpe_leaves.mem"
)(
  input  logic                       clk,
  input  logic                       rst,
  input  logic                       cfg_we,
  input  logic [CAW-1:0]             cfg_addr,
  input  logic [CDW-1:0]             cfg_wdata,
  input  logic                       start,
  input  logic                       s_valid,
  output logic                       s_ready,
  input  logic [XYW-1:0]             s_x,
  input  logic [XYW-1:0]             s_y,
  input  logic                       s_fail,
  input  logic [2:0]                 s_site,
  input  logic                       s_last,
  output logic                       busy,
  output logic                       done,
  output logic                       ck_valid,
  output logic [$bits(ck_rec_t)-1:0] ck_flat
);
  ck_rec_t ck;

  wpe_engine #(.NODE_FILE(NODE_FILE), .LEAF_FILE(LEAF_FILE)) u_engine (
    .clk, .rst, .cfg_we, .cfg_addr, .cfg_wdata, .start,
    .s_valid, .s_ready, .s_x, .s_y, .s_fail, .s_site, .s_last,
    .busy, .done, .ck_valid, .ck
  );

  assign ck_flat = ck;

endmodule
