# Acme Robotics field technician manual — calibration checks

Field technicians supporting **Acme Robotics** deployments should perform the following calibrations after any wheel module replacement or IMU swap. Always power down traction motors before loosening mechanical couplings.

Drive odometry calibration requires a straight 10-meter tape line and reflective fiducials. Command the robot through the service shell: `acme-calibrate --axis all --lidar-skip false`. Capture before-and-after residual plots; residuals above 8 millimeters RMS require mechanical inspection of caster alignment.

Lidar extrinsics use a three-wall corner with known geometry; the wizard projects targets onto flat panels and solves for pitch/roll/yaw offsets. After calibration, run a 20-minute soak test with random goals in an empty aisle to verify collision avoidance margins. Document serial numbers of replaced modules in the service ticket; do not paste those numbers into customer-facing knowledge bases.

This manual is operational fiction for testing retrieval of **Acme Robotics** procedural language distinct from marketing specs or press releases.
