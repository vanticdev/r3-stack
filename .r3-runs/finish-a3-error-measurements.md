# A3 final diagnostic measurements

### Full error measurements

The following are the final stage diagnostics with their numeric evidence (not a substitute for the per-harness status above):

**wiring**

```json
[
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "battery_lead: curvature cannot be met at (229, 26, 40) mm: required 18.0 mm, best 0.0 mm",
    "subjects": [
      "battery_lead",
      "W_BAT_N",
      "W_BAT_P"
    ],
    "data": {
      "kind": "curvature",
      "harness": "battery_lead",
      "position": [
        0.22888102373897382,
        0.025650061726824856,
        0.04
      ],
      "required": 0.018000000000000002,
      "achieved": 0.0,
      "wires": [
        "W_BAT_N",
        "W_BAT_P"
      ],
      "components": [],
      "junction": "",
      "action": "no curve with a 18.0 mm bend radius fits the available space along this segment (the planned chain had to stretch): give the bundle more room, move the connectors, or split the bundle",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "no curve with a 18.0 mm bend radius fits the available space along this segment (the planned chain had to stretch): give the bundle more room, move the connectors, or split the bundle"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "battery_lead: tangent cannot be met at (229, 26, 40) mm: required 18.0 mm, best 0.5 mm",
    "subjects": [
      "battery_lead",
      "W_BAT_N",
      "W_BAT_P"
    ],
    "data": {
      "kind": "tangent",
      "harness": "battery_lead",
      "position": [
        0.22888102373897382,
        0.025650061726824856,
        0.04
      ],
      "required": 0.018000000000000002,
      "achieved": 0.0005174856646935159,
      "wires": [
        "W_BAT_N",
        "W_BAT_P"
      ],
      "components": [],
      "junction": "battery_lead/B:A@0",
      "action": "the bundle cannot leave fan-out breakout battery_lead/B:A@0 along its axis with a 18.0 mm bend radius: rotate the connector or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave fan-out breakout battery_lead/B:A@0 along its axis with a 18.0 mm bend radius: rotate the connector or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "power_y: curvature cannot be met at (168, -21, 75) mm: required 18.0 mm, best 0.0 mm",
    "subjects": [
      "power_y",
      "W_Y_N_IN",
      "W_Y_P_IN",
      "deck",
      "antenna_mast",
      "cap_imu_outboard_post"
    ],
    "data": {
      "kind": "curvature",
      "harness": "power_y",
      "position": [
        0.16811685973902885,
        -0.021000000145503715,
        0.07521364827205951
      ],
      "required": 0.018000000000000002,
      "achieved": 0.0,
      "wires": [
        "W_Y_N_IN",
        "W_Y_P_IN"
      ],
      "components": [
        "deck",
        "antenna_mast",
        "cap_imu_outboard_post"
      ],
      "junction": "",
      "action": "no curve with a 18.0 mm bend radius fits the available space along this segment (the planned chain had to stretch): give the bundle more room, move the connectors, or split the bundle",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "no curve with a 18.0 mm bend radius fits the available space along this segment (the planned chain had to stretch): give the bundle more room, move the connectors, or split the bundle"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "power_y: tangent cannot be met at (172, -23, 69) mm: required 18.1 mm, best 5.6 mm",
    "subjects": [
      "power_y",
      "W_Y_N_HAT",
      "W_Y_N_IN",
      "W_Y_P_IN",
      "deck"
    ],
    "data": {
      "kind": "tangent",
      "harness": "power_y",
      "position": [
        0.1721764800581355,
        -0.023275646622985858,
        0.06909014699791953
      ],
      "required": 0.018073560884329785,
      "achieved": 0.0055777312027080195,
      "wires": [
        "W_Y_N_HAT",
        "W_Y_N_IN",
        "W_Y_P_IN"
      ],
      "components": [
        "deck"
      ],
      "junction": "power_y/B:3",
      "action": "the bundle cannot leave branch power_y/B:3 along its axis with a 18.1 mm bend radius: rotate the branch or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave branch power_y/B:3 along its axis with a 18.1 mm bend radius: rotate the branch or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "power_y: tangent cannot be met at (175, -7, 101) mm: required 18.0 mm, best 3.6 mm",
    "subjects": [
      "power_y",
      "W_Y_N_IN",
      "W_Y_P_IN",
      "antenna_mast",
      "imu_outboard_post",
      "rx_antenna"
    ],
    "data": {
      "kind": "tangent",
      "harness": "power_y",
      "position": [
        0.17450559962679296,
        -0.0069414609058760755,
        0.1009924766761745
      ],
      "required": 0.018000000000000002,
      "achieved": 0.003643827382320516,
      "wires": [
        "W_Y_N_IN",
        "W_Y_P_IN"
      ],
      "components": [
        "antenna_mast",
        "imu_outboard_post",
        "rx_antenna"
      ],
      "junction": "power_y/S:S1",
      "action": "the bundle cannot leave support power_y/S:S1 along its axis with a 18.0 mm bend radius: rotate the support or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave support power_y/S:S1 along its axis with a 18.0 mm bend radius: rotate the support or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "power_y: tangent cannot be met at (190, -35, 52) mm: required 18.0 mm, best 4.5 mm",
    "subjects": [
      "power_y",
      "W_Y_P_ESC",
      "W_Y_P_IN",
      "standoff_fr",
      "power_lead_post"
    ],
    "data": {
      "kind": "tangent",
      "harness": "power_y",
      "position": [
        0.19017766028670222,
        -0.03545497123592596,
        0.05242669475835374
      ],
      "required": 0.018000000000000002,
      "achieved": 0.004477295678256568,
      "wires": [
        "W_Y_P_ESC",
        "W_Y_P_IN"
      ],
      "components": [
        "standoff_fr",
        "power_lead_post"
      ],
      "junction": "power_y/B:6",
      "action": "the bundle cannot leave branch power_y/B:6 along its axis with a 18.0 mm bend radius: rotate the branch or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave branch power_y/B:6 along its axis with a 18.0 mm bend radius: rotate the branch or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "power_y: tangent cannot be met at (203, -34, 53) mm: required 17.7 mm, best 2.1 mm",
    "subjects": [
      "power_y",
      "W_Y_N_HAT",
      "W_Y_N_IN",
      "standoff_fr"
    ],
    "data": {
      "kind": "tangent",
      "harness": "power_y",
      "position": [
        0.20291694540512445,
        -0.03438339841900458,
        0.053354233057273666
      ],
      "required": 0.017666742423459822,
      "achieved": 0.002095425332445213,
      "wires": [
        "W_Y_N_HAT",
        "W_Y_N_IN"
      ],
      "components": [
        "standoff_fr"
      ],
      "junction": "power_y/N:SN",
      "action": "the bundle cannot leave splice power_y/N:SN along its axis with a 17.7 mm bend radius: rotate the splice or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave splice power_y/N:SN along its axis with a 17.7 mm bend radius: rotate the splice or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "power_y: hardware clearance cannot be met at (195, -35, 45) mm: required 1.2 mm, best 0.4 mm",
    "subjects": [
      "power_y",
      "W_Y_P_ESC",
      "W_Y_P_IN",
      "standoff_fr"
    ],
    "data": {
      "kind": "hardware_clearance",
      "harness": "power_y",
      "position": [
        0.19541189747452617,
        -0.03468663937622892,
        0.04504032828048033
      ],
      "required": 0.0011999999999999997,
      "achieved": 0.00042593010791211125,
      "wires": [
        "W_Y_P_ESC",
        "W_Y_P_IN"
      ],
      "components": [
        "standoff_fr"
      ],
      "junction": "",
      "action": "the corridor between standoff_fr leaves 0.4 mm to the part; 1.2 mm is required: move the connector/component or add a routing channel",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the corridor between standoff_fr leaves 0.4 mm to the part; 1.2 mm is required: move the connector/component or add a routing channel"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "power_y: tangent cannot be met at (202, -37, 39) mm: required 18.0 mm, best 9.8 mm",
    "subjects": [
      "power_y",
      "W_Y_P_ESC",
      "W_Y_P_IN",
      "standoff_fr",
      "chassis"
    ],
    "data": {
      "kind": "tangent",
      "harness": "power_y",
      "position": [
        0.20212588214393387,
        -0.03708573287211985,
        0.03873526120586929
      ],
      "required": 0.018000000000000002,
      "achieved": 0.009829911524689806,
      "wires": [
        "W_Y_P_ESC",
        "W_Y_P_IN"
      ],
      "components": [
        "standoff_fr",
        "chassis"
      ],
      "junction": "power_y/N:SP",
      "action": "the bundle cannot leave splice power_y/N:SP along its axis with a 18.0 mm bend radius: rotate the splice or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave splice power_y/N:SP along its axis with a 18.0 mm bend radius: rotate the splice or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "power_y: tangent cannot be met at (187, -8, 70) mm: required 16.0 mm, best 9.8 mm",
    "subjects": [
      "power_y",
      "W_Y_P_ESC",
      "deck",
      "cap_camera_mount_1",
      "antenna_mast"
    ],
    "data": {
      "kind": "tangent",
      "harness": "power_y",
      "position": [
        0.18704551380281143,
        -0.007567226477788235,
        0.0700513441043029
      ],
      "required": 0.016,
      "achieved": 0.009817010741088318,
      "wires": [
        "W_Y_P_ESC"
      ],
      "components": [
        "deck",
        "cap_camera_mount_1",
        "antenna_mast"
      ],
      "junction": "power_y/B:9",
      "action": "the bundle cannot leave branch power_y/B:9 along its axis with a 16.0 mm bend radius: rotate the branch or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave branch power_y/B:9 along its axis with a 16.0 mm bend radius: rotate the branch or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "power_y: tangent cannot be met at (182, 3, 70) mm: required 18.0 mm, best 11.7 mm",
    "subjects": [
      "power_y",
      "W_Y_N_ESC",
      "W_Y_P_ESC",
      "deck",
      "antenna_mast",
      "cap_camera_mount_1"
    ],
    "data": {
      "kind": "tangent",
      "harness": "power_y",
      "position": [
        0.18227909884025967,
        0.002574774475363966,
        0.06997802793722743
      ],
      "required": 0.018000000000000002,
      "achieved": 0.011682418816342133,
      "wires": [
        "W_Y_N_ESC",
        "W_Y_P_ESC"
      ],
      "components": [
        "deck",
        "antenna_mast",
        "cap_camera_mount_1"
      ],
      "junction": "power_y/B:10",
      "action": "the bundle cannot leave branch power_y/B:10 along its axis with a 18.0 mm bend radius: rotate the branch or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave branch power_y/B:10 along its axis with a 18.0 mm bend radius: rotate the branch or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "power_y: tangent cannot be met at (125, -65, 84) mm: required 13.2 mm, best 7.9 mm",
    "subjects": [
      "power_y",
      "W_Y_P_HAT",
      "deck",
      "imu_south_post"
    ],
    "data": {
      "kind": "tangent",
      "harness": "power_y",
      "position": [
        0.12465292863901797,
        -0.06520808924676341,
        0.08357407821447314
      ],
      "required": 0.0132,
      "achieved": 0.007892958731011512,
      "wires": [
        "W_Y_P_HAT"
      ],
      "components": [
        "deck",
        "imu_south_post"
      ],
      "junction": "power_y/B:13",
      "action": "the bundle cannot leave branch power_y/B:13 along its axis with a 13.2 mm bend radius: rotate the branch or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave branch power_y/B:13 along its axis with a 13.2 mm bend radius: rotate the branch or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "esc_power: tangent cannot be met at (163, 40, 39) mm: required 18.0 mm, best 0.9 mm",
    "subjects": [
      "esc_power",
      "W_ESC_N",
      "W_ESC_P",
      "chassis"
    ],
    "data": {
      "kind": "tangent",
      "harness": "esc_power",
      "position": [
        0.16329185321529635,
        0.03965077725160288,
        0.03903766888886064
      ],
      "required": 0.018000000000000002,
      "achieved": 0.0009270978316816253,
      "wires": [
        "W_ESC_N",
        "W_ESC_P"
      ],
      "components": [
        "chassis"
      ],
      "junction": "esc_power/B:A@0",
      "action": "the bundle cannot leave fan-out breakout esc_power/B:A@0 along its axis with a 18.0 mm bend radius: rotate the connector or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave fan-out breakout esc_power/B:A@0 along its axis with a 18.0 mm bend radius: rotate the connector or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "esc_signal: tangent cannot be met at (158, 57, 50) mm: required 7.3 mm, best 2.8 mm",
    "subjects": [
      "esc_signal",
      "W_ESC_BEC",
      "W_ESC_GND",
      "W_ESC_SIG",
      "esc"
    ],
    "data": {
      "kind": "tangent",
      "harness": "esc_signal",
      "position": [
        0.15769503999999998,
        0.056999999999999995,
        0.049600000000000005
      ],
      "required": 0.00732376,
      "achieved": 0.0027864396317358324,
      "wires": [
        "W_ESC_BEC",
        "W_ESC_GND",
        "W_ESC_SIG"
      ],
      "components": [
        "esc"
      ],
      "junction": "esc_signal/B:A@0",
      "action": "the bundle cannot leave fan-out breakout esc_signal/B:A@0 along its axis with a 7.3 mm bend radius: rotate the connector or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave fan-out breakout esc_signal/B:A@0 along its axis with a 7.3 mm bend radius: rotate the connector or give it more room"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "esc_signal: hardware clearance cannot be met at (158, 57, 50) mm: required 1.2 mm, best 0.3 mm",
    "subjects": [
      "esc_signal",
      "W_ESC_BEC",
      "W_ESC_GND",
      "W_ESC_SIG",
      "esc"
    ],
    "data": {
      "kind": "hardware_clearance",
      "harness": "esc_signal",
      "position": [
        0.15769503999999998,
        0.056999999999999995,
        0.049600000000000005
      ],
      "required": 0.0012,
      "achieved": 0.000348108748108847,
      "wires": [
        "W_ESC_BEC",
        "W_ESC_GND",
        "W_ESC_SIG"
      ],
      "components": [
        "esc"
      ],
      "junction": "",
      "action": "the corridor between esc leaves 0.3 mm to the part; 1.2 mm is required: move the connector/component or add a routing channel",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the corridor between esc leaves 0.3 mm to the part; 1.2 mm is required: move the connector/component or add a routing channel"
  },
  {
    "code": "route_infeasible",
    "severity": "error",
    "message": "rtk_radio_lead: tangent cannot be met at (30, -10, 104) mm: required 4.7 mm, best 3.0 mm",
    "subjects": [
      "rtk_radio_lead",
      "W_RTK_5V",
      "W_RTK_GND",
      "W_RTK_RTCM",
      "W_RTK_TX2",
      "gps",
      "gnss_riser",
      "tape_gps"
    ],
    "data": {
      "kind": "tangent",
      "harness": "rtk_radio_lead",
      "position": [
        0.030078399999999998,
        -0.0105,
        0.103546
      ],
      "required": 0.0047071000000000005,
      "achieved": 0.0030243000999391873,
      "wires": [
        "W_RTK_5V",
        "W_RTK_GND",
        "W_RTK_RTCM",
        "W_RTK_TX2"
      ],
      "components": [
        "gps",
        "gnss_riser",
        "tape_gps"
      ],
      "junction": "rtk_radio_lead/B:A@0",
      "action": "the bundle cannot leave fan-out breakout rtk_radio_lead/B:A@0 along its axis with a 4.7 mm bend radius: rotate the connector or give it more room",
      "units": "m"
    },
    "stage": "wiring",
    "hint": "the bundle cannot leave fan-out breakout rtk_radio_lead/B:A@0 along its axis with a 4.7 mm bend radius: rotate the connector or give it more room"
  },
  {
    "code": "cable_support_unplaceable",
    "severity": "error",
    "message": "servo_lead: requested tie has no installed support holding its wires",
    "subjects": [
      "servo_lead"
    ],
    "data": {
      "position": [
        0.15965459999999998,
        -0.067412733,
        0.084586
      ],
      "required_match": 0.004,
      "nearest_support": 0.038659634594924025,
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Place a real saddle or tie post within reach and keep its installation access clear."
  },
  {
    "code": "cable_support_unplaceable",
    "severity": "error",
    "message": "imu_lead: requested tie has no installed support holding its wires",
    "subjects": [
      "imu_lead"
    ],
    "data": {
      "position": [
        0.135,
        -0.065293,
        0.105
      ],
      "required_match": 0.004,
      "nearest_support": null,
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Place a real saddle or tie post within reach and keep its installation access clear."
  },
  {
    "code": "cable_support_unplaceable",
    "severity": "error",
    "message": "imu_lead: requested tie has no installed support holding its wires",
    "subjects": [
      "imu_lead"
    ],
    "data": {
      "position": [
        0.15929300000000002,
        -0.04,
        0.1149
      ],
      "required_match": 0.004,
      "nearest_support": null,
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Place a real saddle or tie post within reach and keep its installation access clear."
  },
  {
    "code": "cable_support_unplaceable",
    "severity": "error",
    "message": "imu_lead: requested tie has no installed support holding its wires",
    "subjects": [
      "imu_lead"
    ],
    "data": {
      "position": [
        0.153293,
        -0.008,
        0.1146
      ],
      "required_match": 0.004,
      "nearest_support": null,
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Place a real saddle or tie post within reach and keep its installation access clear."
  },
  {
    "code": "cable_unsupported_span",
    "severity": "error",
    "message": "power_y: 119 mm between power_y.S6 and connector with no support (limit 99 mm)",
    "subjects": [
      "power_y",
      "W_Y_N_HAT"
    ],
    "data": {
      "wire": "W_Y_N_HAT",
      "harness": "power_y",
      "from": "power_y.S6",
      "to": "connector",
      "start_arc": 0.07613175473096985,
      "end_arc": 0.19490155680870308,
      "length": 0.11876980207773323,
      "limit": 0.09946897424054094,
      "midpoint": [
        0.1217946706711698,
        -0.08096658405811165,
        0.09447526158567808
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "No flat, reachable structure face within the span: add a bracket or stand-off for a tie mount near the midpoint, or reroute along the chassis plate / deck."
  },
  {
    "code": "cable_unsupported_span",
    "severity": "error",
    "message": "battery_lead: 128 mm between battery_lead.S1 and floating with no support (limit 100 mm)",
    "subjects": [
      "battery_lead",
      "W_BAT_N"
    ],
    "data": {
      "wire": "W_BAT_N",
      "harness": "battery_lead",
      "from": "battery_lead.S1",
      "to": "floating",
      "start_arc": 0.015149086887328357,
      "end_arc": 0.14327926489010134,
      "length": 0.128130178002773,
      "limit": 0.1,
      "midpoint": [
        0.1920122985972803,
        0.04927605037864406,
        0.049488263828587356
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "No flat, reachable structure face within the span: add a bracket or stand-off for a tie mount near the midpoint, or reroute along the chassis plate / deck."
  },
  {
    "code": "cable_unsupported_span",
    "severity": "error",
    "message": "servo_lead: 80 mm between servo_lead.S2 and servo_lead.S7 with no support (limit 78 mm)",
    "subjects": [
      "servo_lead",
      "W_SRV_GND",
      "W_SRV_VCC"
    ],
    "data": {
      "wire": "W_SRV_VCC",
      "harness": "servo_lead",
      "from": "servo_lead.S2",
      "to": "servo_lead.S7",
      "start_arc": 0.23660964897684492,
      "end_arc": 0.3165765092675848,
      "length": 0.07996686029073988,
      "limit": 0.07783867888978882,
      "midpoint": [
        0.16359752215734014,
        -0.06589858346727384,
        0.08917417288749477
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "No flat, reachable structure face within the span: add a bracket or stand-off for a tie mount near the midpoint, or reroute along the chassis plate / deck."
  },
  {
    "code": "cable_unsupported_span",
    "severity": "error",
    "message": "esc_signal: 276 mm between connector and connector with no support (limit 74 mm)",
    "subjects": [
      "esc_signal",
      "W_ESC_BEC",
      "W_ESC_GND",
      "W_ESC_SIG"
    ],
    "data": {
      "wire": "W_ESC_SIG",
      "harness": "esc_signal",
      "from": "connector",
      "to": "connector",
      "start_arc": 0.0,
      "end_arc": 0.276370548061457,
      "length": 0.276370548061457,
      "limit": 0.07361527741591041,
      "midpoint": [
        0.12989568414533728,
        -0.0006445362209115454,
        0.08233616723116025
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "No flat, reachable structure face within the span: add a bracket or stand-off for a tie mount near the midpoint, or reroute along the chassis plate / deck."
  },
  {
    "code": "cable_unsupported_span",
    "severity": "error",
    "message": "imu_lead: 183 mm between connector and connector with no support (limit 62 mm)",
    "subjects": [
      "imu_lead",
      "W_IMU_GND",
      "W_IMU_SCL",
      "W_IMU_SDA",
      "W_IMU_VCC"
    ],
    "data": {
      "wire": "W_IMU_VCC",
      "harness": "imu_lead",
      "from": "connector",
      "to": "connector",
      "start_arc": 0.0,
      "end_arc": 0.18267928987758153,
      "length": 0.18267928987758153,
      "limit": 0.061902822886946804,
      "midpoint": [
        0.15577761186018618,
        -0.05126789787267627,
        0.11616303150750809
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "No flat, reachable structure face within the span: add a bracket or stand-off for a tie mount near the midpoint, or reroute along the chassis plate / deck."
  },
  {
    "code": "connector_clearance_violation",
    "severity": "error",
    "message": "connector battery_lead.B body is 7.8 mm from moving wheel_fl (required 8 mm)",
    "subjects": [
      "battery_lead",
      "battery_lead.B",
      "wheel_fl"
    ],
    "data": {
      "min_distance": 0.0077719106550856246,
      "required": 0.008,
      "component": "wheel_fl",
      "connector_type": "xt90h-female",
      "position": [
        0.19367089862799133,
        0.054658199744017345,
        0.09067090262799134
      ],
      "units": "m",
      "distance_router": 0.0077719106550856246,
      "distance_exact": 0.007771910655085636,
      "distance_mujoco": 0.007761910852798606,
      "witness_wire": [
        0.20404100046357512,
        0.057173103102155585,
        0.0853306079028866
      ],
      "witness_part": [
        0.2101834337161544,
        0.06010071413618925,
        0.08157531795380857
      ],
      "witness_centerline": [
        0.20404100046357512,
        0.057173103102155585,
        0.0853306079028866
      ],
      "bundle_radius": 0.0,
      "witness_pose": 47,
      "representations_agree": true
    },
    "stage": "wiring",
    "hint": ""
  },
  {
    "code": "wire_intersects_wire",
    "severity": "error",
    "message": "power_y bundle surfaces overlap by 4.3 mm",
    "subjects": [
      "power_y",
      "W_Y_N_ESC",
      "W_Y_N_HAT",
      "W_Y_N_IN",
      "W_Y_P_ESC",
      "W_Y_P_HAT",
      "W_Y_P_IN"
    ],
    "data": {
      "penetration": 0.00429107736536958,
      "wire_a": "W_Y_N_HAT",
      "wire_b": "W_Y_P_IN",
      "radius_a": 0.0036667424234598208,
      "radius_b": 0.002,
      "arc_length_a": 0.03109014918815415,
      "arc_length_b": 0.10377769972877572,
      "witness": [
        [
          0.17736184482730016,
          -0.026936391767720703,
          0.06733580268535737
        ],
        [
          0.177086546069824,
          -0.026617828632179596,
          0.06602615293261554
        ]
      ],
      "position": [
        0.17722419544856208,
        -0.02677711019995015,
        0.06668097780898646
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Separate the branches or route one bundle around the other; preserve real splice joins."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "power_y bends at 6.7 mm radius; minimum 18.0 mm for a 2-conductor 8.0 mm bundle",
    "subjects": [
      "power_y",
      "W_Y_N_IN",
      "W_Y_P_IN"
    ],
    "data": {
      "radius": 0.006706059217138295,
      "min_radius": 0.018000000000000002,
      "ratio": 0.37255884539657186,
      "conductors": 2,
      "bundle_od": 0.008,
      "position": [
        0.17450559962679296,
        -0.0069414609058760755,
        0.1009924766761745
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "power_y bends at 7.5 mm radius; minimum 18.1 mm for a 3-conductor 8.1 mm bundle",
    "subjects": [
      "power_y",
      "W_Y_N_HAT",
      "W_Y_N_IN",
      "W_Y_P_IN"
    ],
    "data": {
      "radius": 0.0075397051531810476,
      "min_radius": 0.018073560884329785,
      "ratio": 0.4171676628327379,
      "conductors": 3,
      "bundle_od": 0.008147121768659572,
      "position": [
        0.1721764800581355,
        -0.023275646622985858,
        0.06909014699791953
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "power_y bends at 5.3 mm radius; minimum 18.0 mm for a 2-conductor 8.0 mm bundle",
    "subjects": [
      "power_y",
      "W_Y_P_ESC",
      "W_Y_P_IN"
    ],
    "data": {
      "radius": 0.005281877251782158,
      "min_radius": 0.018000000000000002,
      "ratio": 0.2934376250990088,
      "conductors": 2,
      "bundle_od": 0.008,
      "position": [
        0.19017766028670222,
        -0.03545497123592596,
        0.05242669475835374
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "power_y bends at 8.4 mm radius; minimum 18.0 mm for a 2-conductor 8.0 mm bundle",
    "subjects": [
      "power_y",
      "W_Y_P_ESC",
      "W_Y_P_IN"
    ],
    "data": {
      "radius": 0.008415354691030982,
      "min_radius": 0.018000000000000002,
      "ratio": 0.46751970505727675,
      "conductors": 2,
      "bundle_od": 0.008,
      "position": [
        0.19541189747452617,
        -0.03468663937622892,
        0.04504032828048033
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "power_y bends at 3.1 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle",
    "subjects": [
      "power_y",
      "W_Y_P_IN"
    ],
    "data": {
      "radius": 0.0030899052187983275,
      "min_radius": 0.016,
      "ratio": 0.19311907617489546,
      "conductors": 1,
      "bundle_od": 0.004,
      "position": [
        0.21340974582150915,
        0.002882131480732932,
        0.11532909937200866
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "power_y bends at 6.7 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle",
    "subjects": [
      "power_y",
      "W_Y_N_ESC",
      "W_Y_P_ESC"
    ],
    "data": {
      "radius": 0.006726089792649831,
      "min_radius": 0.016,
      "ratio": 0.42038061204061444,
      "conductors": 1,
      "bundle_od": 0.004,
      "position": [
        0.179013023306156,
        0.04601841220642164,
        0.06370344053265065
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "power_y bends at 9.4 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle",
    "subjects": [
      "power_y",
      "W_Y_P_FUSE"
    ],
    "data": {
      "radius": 0.009378525819515043,
      "min_radius": 0.016,
      "ratio": 0.5861578637196901,
      "conductors": 1,
      "bundle_od": 0.004,
      "position": [
        0.17099999999999999,
        -0.037,
        0.039
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "power_y bends at 10.5 mm radius; minimum 14.8 mm for a 2-conductor 6.6 mm bundle",
    "subjects": [
      "power_y",
      "W_Y_N_HAT",
      "W_Y_P_HAT"
    ],
    "data": {
      "radius": 0.010536559118928942,
      "min_radius": 0.01485,
      "ratio": 0.7095326006012755,
      "conductors": 2,
      "bundle_od": 0.0066,
      "position": [
        0.12465292863901797,
        -0.06520808924676341,
        0.08357407821447314
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "power_y bends at 4.5 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle",
    "subjects": [
      "power_y",
      "W_Y_N_IN"
    ],
    "data": {
      "radius": 0.0045263338280394525,
      "min_radius": 0.016,
      "ratio": 0.28289586425246577,
      "conductors": 1,
      "bundle_od": 0.004,
      "position": [
        0.22324844492250823,
        0.007801481031232477,
        0.11532909937200866
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "battery_lead bends at 0.8 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle",
    "subjects": [
      "battery_lead",
      "W_BAT_N",
      "W_BAT_P"
    ],
    "data": {
      "radius": 0.0007738972485546314,
      "min_radius": 0.016,
      "ratio": 0.04836857803466446,
      "conductors": 1,
      "bundle_od": 0.004,
      "position": [
        0.2068679568179823,
        0.023799417316909644,
        0.04
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "battery_lead bends at 5.8 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle",
    "subjects": [
      "battery_lead",
      "W_BAT_P"
    ],
    "data": {
      "radius": 0.005783495444923974,
      "min_radius": 0.016,
      "ratio": 0.36146846530774834,
      "conductors": 1,
      "bundle_od": 0.004,
      "position": [
        0.1887515490774918,
        0.052198524968767576,
        0.09067090262799134
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "battery_lead bends at 6.1 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle",
    "subjects": [
      "battery_lead",
      "W_BAT_N"
    ],
    "data": {
      "radius": 0.006126067546672305,
      "min_radius": 0.016,
      "ratio": 0.3828792216670191,
      "conductors": 1,
      "bundle_od": 0.004,
      "position": [
        0.19859024817849086,
        0.057117874519267114,
        0.09067090262799134
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "esc_power bends at 1.4 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle",
    "subjects": [
      "esc_power",
      "W_ESC_N",
      "W_ESC_P"
    ],
    "data": {
      "radius": 0.0013515442813392688,
      "min_radius": 0.016,
      "ratio": 0.0844715175837043,
      "conductors": 1,
      "bundle_od": 0.004,
      "position": [
        0.146,
        0.040999999999999995,
        0.0396
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "esc_power bends at 1.0 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle",
    "subjects": [
      "esc_power",
      "W_ESC_N",
      "W_ESC_P"
    ],
    "data": {
      "radius": 0.0010402110454284979,
      "min_radius": 0.016,
      "ratio": 0.06501319033928112,
      "conductors": 1,
      "bundle_od": 0.004,
      "position": [
        0.15498697069384404,
        0.04998159379357838,
        0.04029656146734936
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "wire_bend_radius_violation",
    "severity": "error",
    "message": "esc_signal bends at 3.2 mm radius; minimum 6.4 mm for a 1-conductor 1.6 mm bundle",
    "subjects": [
      "esc_signal",
      "W_ESC_BEC",
      "W_ESC_GND",
      "W_ESC_SIG"
    ],
    "data": {
      "radius": 0.003182155782744225,
      "min_radius": 0.0064,
      "ratio": 0.4972118410537851,
      "conductors": 1,
      "bundle_od": 0.0016,
      "position": [
        0.146,
        0.05859999999999999,
        0.049600000000000005
      ],
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Give the bundle more room (move the connector or rotate its exit) or split the bundle."
  },
  {
    "code": "cable_sags_into_moving_part",
    "severity": "error",
    "message": "battery_lead cable enters the wheel_fl envelope by 0.2 mm during the slack simulation (t = 1.251 s)",
    "subjects": [
      "battery_lead",
      "W_BAT_N",
      "W_BAT_P",
      "wheel_fl"
    ],
    "data": {
      "min_distance": -0.00017427297149247073,
      "required": 0.008,
      "component": "wheel_fl",
      "time": 1.250999999999973,
      "worst_wire": "W_BAT_P",
      "load_case": "0.5 g 25 Hz + 3 g bump + lock-to-lock steering",
      "per_wire_min_distance": {
        "W_BAT_P": -0.00017427297149247073,
        "W_BAT_N": -0.00017427297149247073
      },
      "units": "m"
    },
    "stage": "wiring",
    "hint": "Add a tie point or shorten the slack on this span."
  }
]
```

**connectors**

```json
[
  {
    "code": "connector_body_interference",
    "severity": "error",
    "message": "power_y.P2 / esc_power.B: housings/part bodies intersect by 8.360 mm^3 outside contact channels",
    "subjects": [
      "power_y.P2",
      "esc_power.B"
    ],
    "data": {
      "volume": 8.360044939036505e-09,
      "limit": 1e-12
    },
    "stage": "connectors",
    "hint": ""
  }
]
```

**electrical**

No errors.

