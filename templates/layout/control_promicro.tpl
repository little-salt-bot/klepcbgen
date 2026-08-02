  (module Connector_PinHeader_2.54mm:PinHeader_2x12_P2.54mm_Vertical (layer B.Cu) (tedit 5A00F000) (tstamp PROMICRO0001)
    (at 0 0)
    (descr "Pro Micro module socket, 2x12 pin header (24 pins)")
    (tags "Pro Micro ATmega32U4 2x12")
    (path /PRO_MICRO)
    (attr smd)
    (fp_text reference U1 (at 0 -3) (layer F.SilkS)
      (effects (font (size 1 1) (thickness 0.15)))
    )
    (fp_text value Pro_Micro (at 0 32) (layer F.Fab)
      (effects (font (size 1 1) (thickness 0.15)))
    )
    (fp_line (start -2.54 -2.54) (end 5.08 -2.54) (layer F.CrtYd) (width 0.05))
    (fp_line (start 5.08 -2.54) (end 5.08 30.48) (layer F.CrtYd) (width 0.05))
    (fp_line (start 5.08 30.48) (end -2.54 30.48) (layer F.CrtYd) (width 0.05))
    (fp_line (start -2.54 30.48) (end -2.54 -2.54) (layer F.CrtYd) (width 0.05))
    {# Pro Micro has 24 GPIO/signal pins on the 2x12 header. Matrix lines map to
       the Pro Micro pins in CONTROLLER_PINS order (D3,D2,D1,D0,D4,C6,D7,E6,B4,
       B5,B6,B2,B3,B1,F7,F6,F5,F4) on the header's signal pins. The 2x12 header
       has pads at (0, y) odd column and (2.54, y) even column, y stepping 2.54.
       We wire matrix lines to the first `matrix_lines` signal pads. #}
    {% for line in range(matrix_lines) %}
    (pad "{{ line + 1 }}" thru_hole circle (at {{ (line % 2) * 2.54 }} {{ (line // 2) * 2.54 }}) (size 1.7 1.7) (drill 1)
      (layers *.Cu *.Mask)
      (net {{ nets.get_net_num("/Matrix_" ~ line) }} /Matrix_{{ line }}))
    {% endfor %}
    (pad "23" thru_hole circle (at 2.54 27.94) (size 1.7 1.7) (drill 1) (layers *.Cu *.Mask)
      (net {{ nets.get_net_num("VCC") }} VCC))
    (pad "24" thru_hole circle (at 0 30.48) (size 1.7 1.7) (drill 1) (layers *.Cu *.Mask)
      (net {{ nets.get_net_num("GND") }} GND))
  )
