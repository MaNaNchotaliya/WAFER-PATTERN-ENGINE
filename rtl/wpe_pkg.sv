// wpe_pkg.sv - shared constants and the checkpoint record of the wafer-pattern engine.
// Keep every constant in sync with model/wpe_params.py.
package wpe_pkg;

  // ---------------------------------------------------------------- geometry
  localparam int XYW   = 8;              // die coordinate width: maps up to 256 x 256
  localparam int DW    = XYW + 1;        // map width / height registers (1..256)
  localparam int NR    = 5;              // rings
  localparam int NS    = 8;              // sectors
  localparam int NZ    = NR * NS;        // zones
  localparam int THW   = 36;             // squared-radius width (ring thresholds)

  // ---------------------------------------------------------------- fixed point
  localparam int QB    = 12;             // Q.12 rates
  localparam int ONE   = 1 << QB;
  localparam int TMIN  = 4;              // dies a sector needs before its rate counts
  localparam int CW    = 17;             // die counters (up to 65,536 dies)
  localparam int DVW   = 19;             // divider operand width
  localparam int FW    = 14;             // signed feature width
  localparam int NSITE = 8;

  // ---------------------------------------------------------------- classifier
  localparam int NCLS  = 9;
  localparam int CLW   = 4;              // class index width
  localparam int NF    = 39;
  localparam int FIW   = 6;              // feature index width
  localparam int DEPTH = 3;
  localparam int NNODE = 7;
  localparam int NLEAF = 8;
  localparam int NITER = 64;
  localparam int NTREE = NITER * NCLS;   // 576
  localparam int LW    = 16;             // leaf / base value width (Q.8)
  localparam int SCW   = 20;             // score accumulator width
  localparam int MGW   = SCW + 1;        // margin width
  localparam int NODE_WORDS = NTREE * NNODE;          // 4032
  localparam int LEAF_WORDS = NTREE * NLEAF + NCLS;   // 4617 (bases at the end)
  localparam int RUN_MAX = 15;

  // feature indices (model/wpe_params.py FEATURES)
  localparam int F_FRAC  = 0;
  localparam int F_F     = 1;
  localparam int F_FR    = 2;    // 5 ring fail rates
  localparam int F_COV   = 7;    // 5 ring coverages
  localparam int F_DR    = 12;   // 5 ring rate - wafer rate
  localparam int F_SMAX  = 17;   // 5 highest sector rates
  localparam int F_SMIN  = 22;   // 5 lowest sector rates
  localparam int F_SCONC = 27;   // 5 worst-sector shares
  localparam int F_ZCONC = 32;
  localparam int F_Q     = 33;
  localparam int F_LIFT  = 34;
  localparam int F_QMAX  = 35;
  localparam int F_QMIN  = 36;
  localparam int F_ANISO = 37;
  localparam int F_FFPF  = 38;

  // ---------------------------------------------------------------- configuration bus
  localparam int CAW = 5;
  localparam int CDW = 36;
  localparam logic [CAW-1:0] R_W = 0, R_H = 1, R_CX2 = 2, R_CY2 = 3, R_WX = 4, R_WY = 5,
                             R_THR0 = 6, R_NTOT = 10, R_NR0 = 11, R_CHK = 16, R_PERSIST = 17,
                             R_MINFRAC = 18, R_MARGIN0 = 19, R_NSITES = 28, R_SITEMIN = 29,
                             R_SITEDELTA = 30;

  // ---------------------------------------------------------------- checkpoint record
  typedef struct packed {
    logic [CW-1:0]                ndies;      // accepted dies so far
    logic [CW-1:0]                n_dup;      // rejected: already tested
    logic [CW-1:0]                n_oor;      // rejected: outside the W x H map
    logic                         final_ck;   // checkpoint of the last die
    logic [NF-1:0][FW-1:0]        feat;       // features (signed)
    logic [NCLS-1:0][SCW-1:0]     score;      // class scores (signed, Q.8)
    logic [CLW-1:0]               top;
    logic [MGW-1:0]               margin;
    logic [CLW-1:0]               run_cls;
    logic [3:0]                   run_len;
    logic                         called;     // early call latched
    logic [CLW-1:0]               call_cls;
    logic [CW-1:0]                call_ndies;
    logic                         site_flag;  // probe-site fault latched
    logic [2:0]                   site_id;
    logic [CW-1:0]                site_ndies;
  } ck_rec_t;

endpackage
