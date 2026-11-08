# build_ooc.tcl - out-of-context synthesis + place & route of the wafer-pattern engine.
# Usage (from the repo root):
#   vivado -mode batch -source scripts/build_ooc.tcl -tclargs <part> <clock period ns> <out dir>
# Example:
#   vivado -mode batch -source scripts/build_ooc.tcl -tclargs xc7a35tcpg236-1 10.0 build/impl_100mhz
# Out-of-context mode is the usual way to characterise an IP core: no I/O pins,
# every register-to-register path is timed against the clock constraint.

set part   [lindex $argv 0]
set period [lindex $argv 1]
set outdir [file normalize [lindex $argv 2]]
set root   [file normalize [file dirname [info script]]/..]
file mkdir $outdir

set srcs [list wpe_pkg.sv wpe_zone.sv wpe_div.sv wpe_gbt.sv wpe_engine.sv wpe_engine_top.sv]
foreach s $srcs { read_verilog -sv [file join $root rtl $s] }

# tree ROM images: absolute paths, so $readmemh does not depend on the working directory
set node_mem [file join $root rtl wpe_nodes.mem]
set leaf_mem [file join $root rtl wpe_leaves.mem]

set xdc [file join $outdir clock.xdc]
set fh [open $xdc w]
puts $fh "create_clock -period $period -name clk \[get_ports clk\]"
close $fh
read_xdc -mode out_of_context $xdc

synth_design -top wpe_engine_top -part $part -mode out_of_context \
  -generic "NODE_FILE=\"$node_mem\"" -generic "LEAF_FILE=\"$leaf_mem\""
report_utilization -file [file join $outdir util_synth.txt]

opt_design
place_design
phys_opt_design
route_design

report_utilization                 -file [file join $outdir util_routed.txt]
report_utilization -hierarchical   -file [file join $outdir util_hier.txt]
report_timing_summary -max_paths 5 -file [file join $outdir timing_summary.txt]
report_timing -max_paths 3 -sort_by group -file [file join $outdir timing_paths.txt]
report_power                       -file [file join $outdir power.txt]
report_drc                         -file [file join $outdir drc.txt]
# routed netlist for a gate-level functional re-run of the testbench
write_verilog -force -mode funcsim [file join $outdir wpe_engine_funcsim.v]

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
set fh [open [file join $outdir summary.txt] w]
puts $fh "part $part"
puts $fh "period_ns $period"
puts $fh "wns_ns $wns"
puts $fh "whs_ns $whs"
puts $fh "fmax_mhz [format %.1f [expr {1000.0 / ($period - $wns)}]]"
close $fh
puts "BUILD_DONE part=$part period=$period WNS=$wns WHS=$whs"
