# create_project.tcl
# Makes the Vivado project (vivado/wafer_pattern_engine.xpr) from rtl/, tb/, constraints/, sim/.
#
#   GUI:    vivado -source scripts/create_project.tcl
#   batch:  vivado -mode batch -source scripts/create_project.tcl -tclargs build
#           (batch also runs synthesis + implementation and writes the reports and the
#            routed netlist to build/impl)
#
# The engine is built out of context (no I/O pins, every reg-to-reg path timed at
# 100 MHz) since it is a block that would sit inside a tester FPGA, it has no board top.

set root [file normalize [file join [file dirname [info script]] ..]]
set proj [file join $root vivado]

create_project wafer_pattern_engine $proj -part xc7a35tcpg236-1 -force
set_property target_language Verilog [current_project]
set_property simulator_language Verilog [current_project]

# design sources
set rtl [file join $root rtl]
add_files -norecurse [list $rtl/wpe_defs.vh $rtl/wpe_zone.v $rtl/wpe_div.v $rtl/wpe_gbt.v \
                           $rtl/wpe_engine.v $rtl/wpe_nodes.mem $rtl/wpe_leaves.mem]
set_property file_type {Verilog Header} [get_files wpe_defs.vh]
set_property include_dirs $rtl [get_filesets sources_1]
set_property top wpe_engine [get_filesets sources_1]

# 100 MHz clock
add_files -fileset constrs_1 -norecurse [file join $root constraints wpe_clock.xdc]
set_property USED_IN {synthesis implementation out_of_context} [get_files wpe_clock.xdc]
set_property -name {STEPS.SYNTH_DESIGN.ARGS.MORE OPTIONS} -value {-mode out_of_context} -objects [get_runs synth_1]

# testbench. The default run is one WM-811K wafer with a Center pattern (sim/demo_center.*),
# the full regression is scripts/run_regression.py
add_files -fileset sim_1 -norecurse [file join $root tb tb_wpe_engine.v]
set_property include_dirs $rtl [get_filesets sim_1]
set_property top tb_wpe_engine [get_filesets sim_1]
set lst [file join $proj demo_list.txt]
set fh [open $lst w]
puts $fh "$root/sim/demo_center.cfg $root/sim/demo_center.die $proj/demo_center.out"
close $fh
set_property -name {xsim.simulate.runtime} -value {all} -objects [get_filesets sim_1]
set_property -name {xsim.simulate.xsim.more_options} -value "-testplusarg LIST:$lst" -objects [get_filesets sim_1]

puts "project ready: $proj/wafer_pattern_engine.xpr"

if {[lsearch $argv build] >= 0} {
    launch_runs synth_1 -jobs 8
    wait_on_run synth_1
    launch_runs impl_1 -jobs 8
    wait_on_run impl_1
    open_run impl_1
    set out [file join $root build impl]
    file mkdir $out
    report_utilization                 -file $out/util_routed.txt
    report_utilization -hierarchical   -file $out/util_hier.txt
    report_timing_summary -max_paths 5 -file $out/timing_summary.txt
    report_timing -max_paths 3 -sort_by group -file $out/timing_paths.txt
    write_verilog -force -mode funcsim $out/wpe_engine_funcsim.v
    set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
    set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
    set fh [open $out/summary.txt w]
    puts $fh "part xc7a35tcpg236-1"
    puts $fh "period_ns 10.0"
    puts $fh "wns_ns $wns"
    puts $fh "whs_ns $whs"
    puts $fh "fmax_mhz [format %.1f [expr {1000.0 / (10.0 - $wns)}]]"
    close $fh
    puts "BUILD_DONE WNS=$wns WHS=$whs"
}
