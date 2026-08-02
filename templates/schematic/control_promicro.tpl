$Comp
L Connector_Generic:Conn_02x12_Odd_Even J1
U 1 1 PRO_MICRO_HEADER
P 12000 12000
F 0 "J1" H 12000 11800 60  0000 C CNN
F 1 "Pro_Micro_Header" H 12000 11850 60  0000 C CNN
F 2 "Connector_PinHeader_2.54mm:PinHeader_2x12_P2.54mm_Vertical" H 12000 12000 60  0001 C CNN
F 3 "~" H 12000 12000 60  0000 C CNN
	1    12000 12000
	1    0    0    -1  
$EndComp
{# Each duplex matrix line N connects to the Pro Micro header's signal pin N
   (see CONTROLLER_PINS promicro for the GPIO->pin order). We place a net
   label near each matrix pin using INTEGER coordinates only — the EESchema
   v4 loader rejects non-integer coords on Text Label lines. Odd pins on the
   left, even pins on the right. Exact pin alignment is cosmetic here (labels
   are net-name references, not wires). #}
{% for line in range(matrix_lines) %}
{% if (line + 1) % 2 == 1 %}
Text Label 11990 {{ 12000 + 12 - ((line // 2) * 2) }} 2    50   ~ 0
Matrix_{{ line }}
{% else %}
Text Label 12010 {{ 12000 + 12 - (((line + 1) // 2 - 1) * 2) }} 0    50   ~ 0
Matrix_{{ line }}
{% endif %}
{% endfor %}
$Comp
L power:GND #PWR?
U 1 1 PWRG
P 11950 12500
F 0 "#PWR?" H 11950 12300 50  0001 C CNN
F 1 "GND" H 11950 12300 50  0000 C CNN
F 2 "" H 11950 12500 50  0001 C CNN
F 3 "" H 11950 12500 50  0001 C CNN
	1    11950 12500
	1    0    0    -1  
$EndComp
$Comp
L power:VCC #PWR?
U 1 1 PWRV
P 12050 12500
F 0 "#PWR?" H 12050 12300 50  0001 C CNN
F 1 "VCC" H 12050 12300 50  0000 C CNN
F 2 "" H 12050 12500 50  0001 C CNN
F 3 "" H 12050 12500 50  0001 C CNN
	1    12050 12500
	1    0    0    -1  
$EndComp
