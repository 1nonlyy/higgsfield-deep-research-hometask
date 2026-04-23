# Acme Robotics Model R-7 mobile platform — technical specifications

**Acme Robotics** publishes this specification sheet for integrators evaluating the Model R-7 autonomous mobile robot for warehouse and light manufacturing environments. The platform uses a holonomic drive base with four independently steered wheels, enabling translation and rotation without sweeping aisle tails beyond the chassis footprint.

Rated payload on flat concrete is 300 kilograms with a center of gravity height limit of 350 millimeters above the deck. Maximum sustained speed is 1.8 meters per second with a software-enforced cap in human-shared aisles. The safety stack combines 2D lidar, depth cameras, and bumper strips; the controller runs a deterministic scheduler alongside a higher-level behavior tree for mission allocation.

Power is supplied by a 48 V, 105 Ah lithium pack with hot-swappable modules and a predicted eight-hour duty cycle at 50% average load. Ingress protection is IP54 for electronics bays; customers requiring wash-down should request the R-7W variant. Onboard compute includes an ARM-based motion controller and an optional GPU module for customer perception models. **Acme Robotics** provides ROS 2 reference drivers, calibration wizards, and a digital twin for offline path tuning before deployment.
