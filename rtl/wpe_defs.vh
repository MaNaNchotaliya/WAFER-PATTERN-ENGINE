// wpe_defs.vh
// constants shared by all modules, `include this inside the module body.
// must match model/wpe_params.py

// geometry
localparam XYW   = 8;              // die coordinate width: maps up to 256 x 256
localparam DW    = 9;              // map width / height registers (1..256)
localparam NR    = 5;              // rings
localparam NS    = 8;              // sectors
localparam NZ    = 40;             // zones (NR x NS)
localparam THW   = 36;             // squared-radius width (ring thresholds)

// fixed point, rates are Q.12
localparam QB    = 12;             // Q.12 rates
localparam ONE   = 4096;           // 1.0 in Q.12
localparam TMIN  = 4;              // dies a sector needs before its rate counts
localparam CW    = 17;             // die counters (up to 65,536 dies)
localparam DVW   = 19;             // divider operand width
localparam FW    = 14;             // signed feature width
localparam NSITE = 8;

// tree ensemble
localparam NCLS  = 9;
localparam CLW   = 4;              // class index width
localparam NF    = 39;
localparam FIW   = 6;              // feature index width
localparam DEPTH = 3;
localparam NNODE = 7;
localparam NLEAF = 8;
localparam NITER = 64;
localparam NTREE = 576;            // NITER x NCLS
localparam LW    = 16;             // leaf / start value width (Q.8)
localparam SCW   = 20;             // score accumulator width
localparam MGW   = 21;             // margin width
localparam NODE_WORDS = 4032;      // NTREE x NNODE
localparam LEAF_WORDS = 4617;      // NTREE x NLEAF + NCLS (start values at the end)
localparam NAW   = 12;             // node ROM address width
localparam LAW   = 13;             // leaf ROM address width
localparam RUN_MAX = 15;

// feature index (same order as FEATURES in wpe_params.py)
localparam F_FRAC  = 0;
localparam F_F     = 1;
localparam F_FR    = 2;            // 5 ring fail rates
localparam F_COV   = 7;            // 5 ring coverages
localparam F_DR    = 12;           // 5 ring rate - wafer rate
localparam F_SMAX  = 17;           // 5 highest sector rates
localparam F_SMIN  = 22;           // 5 lowest sector rates
localparam F_SCONC = 27;           // 5 worst-sector shares
localparam F_ZCONC = 32;
localparam F_Q     = 33;
localparam F_LIFT  = 34;
localparam F_QMAX  = 35;
localparam F_QMIN  = 36;
localparam F_ANISO = 37;
localparam F_FFPF  = 38;

// config register addresses
localparam CAW = 5;
localparam CDW = 36;
localparam R_W = 0, R_H = 1, R_CX2 = 2, R_CY2 = 3, R_WX = 4, R_WY = 5, R_THR0 = 6,
           R_NTOT = 10, R_NR0 = 11, R_CHK = 16, R_PERSIST = 17, R_MINFRAC = 18,
           R_MARGIN0 = 19, R_NSITES = 28, R_SITEMIN = 29, R_SITEDELTA = 30;

// ck_flat layout, bit offsets from the LSB
localparam CK_NDIES  = 0;                      // [CW]  accepted dies so far
localparam CK_NDUP   = CK_NDIES  + CW;         // [CW]  rejected: already tested
localparam CK_NOOR   = CK_NDUP   + CW;         // [CW]  rejected: outside the map
localparam CK_FINAL  = CK_NOOR   + CW;         // [1]   checkpoint of the last die
localparam CK_FEAT   = CK_FINAL  + 1;          // [NF x FW] features, feature i at CK_FEAT + i*FW
localparam CK_SCORE  = CK_FEAT   + NF * FW;    // [NCLS x SCW] class scores
localparam CK_TOP    = CK_SCORE  + NCLS * SCW; // [CLW]
localparam CK_MARGIN = CK_TOP    + CLW;        // [MGW]
localparam CK_RUNCLS = CK_MARGIN + MGW;        // [CLW]
localparam CK_RUNLEN = CK_RUNCLS + CLW;        // [4]
localparam CK_CALLED = CK_RUNLEN + 4;          // [1]   early call latched
localparam CK_CALLCLS= CK_CALLED + 1;          // [CLW]
localparam CK_CALLN  = CK_CALLCLS + CLW;       // [CW]
localparam CK_SITEF  = CK_CALLN  + CW;         // [1]   probe-site fault latched
localparam CK_SITEID = CK_SITEF  + 1;          // [3]
localparam CK_SITEN  = CK_SITEID + 3;          // [CW]
localparam CK_BITS   = CK_SITEN  + CW;         // 854
