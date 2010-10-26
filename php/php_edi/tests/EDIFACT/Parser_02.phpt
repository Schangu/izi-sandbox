--TEST--
EDI_EDIFACT_Parser test 02
--FILE--
<?php

require_once dirname(__FILE__) . '/../tests.inc.php';

try {
    $parser = EDI::parserFactory('EDIFACT');
    $edidoc = $parser->parse(TEST_DATA_DIR . '/EDIFACT/ex2.edi');
    echo $edidoc->toEDI();
} catch (Exception $exc) {
    echo $exc->getMessage();
    exit(1);
}

?>
--EXPECT--
UNB+UNOA:2+FHPEDAL+HUBERGMBH+990802:1557+9908021557'
UNH+INVOIC0001+INVOIC:D:94B:UN'
BGM+380+9908001+9'
DTM+3:19990802:102'
RFF+ON:O0010001'
DTM+4:19999715:102'
NAD+SE++Fahrradhandel Pedal++Wagingerstr. 5+München++81549'
NAD+BY++Huber GmbH++Obstgasse 2+München++81549'
LIN+1++4711.001'
IMD+F++:::Fahrrad, Damen'
QTY+47:1:PCE'
MOA+66:750'
PRI+AAA:750'
LIN+2++4711.002'
IMD+F++:::Luftpumpe, Stand-'
QTY+47:1:PCE'
MOA+66:19,9'
PRI+AAA:19,9'
LIN+3++4711.003'
IMD+F++:::Ersatzventil'
QTY+47:3:PCE'
MOA+66:7,5'
PRI+AAA:2,5'
UNS+S'
MOA+79:777,4'
MOA+124:124,38'
MOA+128:901,78'
TAX+7+VAT+++:::16+S'
UNT+28+INVOIC0001'
UNZ+1+9908021557'