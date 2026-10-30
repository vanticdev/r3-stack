"""Synthetic passive signal-filter coupon, authored through KiCad's standard libraries."""
from r3_kicad.intent import BoardIntent, NetClass, Part, Placement, PowerTrack, Zone
from r3_kicad.schematic_layout import Group


def build() -> BoardIntent:
    parts = [
        Part('J1', 'Connector_Generic:Conn_01x03', 'INPUT',
             'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical',
             pins={'1': 'SIGNAL_IN', '2': 'SUPPLY', '3': 'GND'}, pcb=Placement(8, 8), connector='INPUT'),
        Part('J2', 'Connector_Generic:Conn_01x03', 'OUTPUT',
             'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical',
             pins={'1': 'SIGNAL_OUT', '2': 'SUPPLY', '3': 'GND'}, pcb=Placement(30, 8), connector='OUTPUT'),
        Part('R1', 'Device:R', '1k', 'Resistor_SMD:R_0805_2012Metric',
             pins={'1': 'SIGNAL_IN', '2': 'SIGNAL_OUT'}, pcb=Placement(19, 8)),
        Part('C1', 'Device:C', '10n', 'Capacitor_SMD:C_0805_2012Metric',
             pins={'1': 'SIGNAL_OUT', '2': 'GND'}, pcb=Placement(24, 16)),
        Part('H1', 'Mechanical:MountingHole', 'M2.5', 'MountingHole:MountingHole_2.7mm_M2.5', pcb=Placement(4, 24), in_bom=False),
        Part('H2', 'Mechanical:MountingHole', 'M2.5', 'MountingHole:MountingHole_2.7mm_M2.5', pcb=Placement(34, 24), in_bom=False),
    ]
    outline = ((1, 1), (37, 1), (37, 27), (1, 27))
    return BoardIntent(name='test-board', title='Signal filter coupon', component='filter', catalog_type='test-filter',
                       width=38, height=28, corner_radius=1, thickness=1.6, copper_oz=1,
                       holes=((4, 24), (34, 24)), parts=parts,
                       net_classes=[NetClass('signal', .25, .2, .6, .3, ('SIGNAL_IN', 'SIGNAL_OUT', 'GND')),
                                    NetClass('supply', .8, .2, .6, .3, ('SUPPLY',), .2)],
                       power_tracks=[PowerTrack('SUPPLY', .8, ('J1.2', (12, 10.54), (26, 10.54), 'J2.2'), current=.2)],
                       zones=[Zone('GND', layer, outline) for layer in ('F.Cu', 'B.Cu')],
                       rules=dict(min_clearance=.2, min_track_width=.2, min_via_diameter=.6, min_via_drill=.3, min_annular_ring=.15, copper_edge_clearance=.3, hole_clearance=.25, hole_to_hole=.25), frame_origin=(19, 14), sheet='A3', notes=['Passive filter: no regulator or safety arbiter is declared.'])


def schematic_groups() -> list[Group]:
    return [Group('Signal filter', (20, 20, 220, 120), ('J1', 'R1', 'C1', 'J2', 'H1', 'H2'))]
