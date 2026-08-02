$Comp
L MCU_Module:RaspberryPi_Pico U1
U 1 1 RPI_PICO
P 12000 12000
F 0 "U1" H 12000 11800 60  0000 C CNN
F 1 "RaspberryPi_Pico" H 12000 11850 60  0000 C CNN
F 2 "Module:RaspberryPi_Pico_SMD" H 12000 12000 60  0001 C CNN
F 3 "~" H 12000 12000 60  0000 C CNN
	1    12000 12000
	1    0    0    -1  
$EndComp
{# Each duplex matrix line N connects to Pico GPIO_N (per CONTROLLER_PINS
   rp2040). We drop a net label near each GPIO carrying a matrix line, using
   INTEGER coordinates only — the EESchema v4 loader rejects non-integer
   coords on Text Label lines. GPIO0-15 map to left-edge pins (y decreasing
   from 12012), GPIO16-28 to right-edge pins. Exact pin alignment is cosmetic
   here (labels are net-name references, not wires). #}
{% for line in range(matrix_lines) %}
{% if line < 16 %}
Text Label 11970 {{ 12000 + 12 - (line * 2) }} 2    50   ~ 0
Matrix_{{ line }}
{% else %}
Text Label 12030 {{ 12000 + 40 - ((line - 15) * 2) }} 0    50   ~ 0
Matrix_{{ line }}
{% endif %}
{% endfor %}
$Comp
L power:GND #PWR?
U 1 1 PWRG
P 11950 12400
F 0 "#PWR?" H 11950 12200 50  0001 C CNN
F 1 "GND" H 11950 12200 50  0000 C CNN
F 2 "" H 11950 12400 50  0001 C CNN
F 3 "" H 11950 12400 50  0001 C CNN
	1    11950 12400
	1    0    0    -1  
$EndComp
$Comp
L power:VCC #PWR?
U 1 1 PWRV
P 12050 12400
F 0 "#PWR?" H 12050 12200 50  0001 C CNN
F 1 "VCC" H 12050 12200 50  0000 C CNN
F 2 "" H 12050 12400 50  0001 C CNN
F 3 "" H 12050 12400 50  0001 C CNN
	1    12050 12400
	1    0    0    -1  
$EndComp
