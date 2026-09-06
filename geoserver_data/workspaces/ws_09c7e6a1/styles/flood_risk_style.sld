<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor version="1.0.0" 
    xmlns="http://www.opengis.net/sld" 
    xmlns:ogc="http://www.opengis.net/ogc" 
    xmlns:xlink="http://www.w3.org/1999/xlink" 
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <NamedLayer>
    <Name>flood_risk_style</Name>
    <UserStyle>
      <Title>Klasifikasi Risiko Banjir</Title>
      <FeatureTypeStyle>
        <Rule>
          <RasterSymbolizer>
            <ColorMap type="values">
              <!-- Nilai 0 dibuat transparan -->
              <ColorMapEntry color="#000000" quantity="0" opacity="0" label="No Data"/>
              <!-- Nilai 1 s/d 5 diberi warna -->
              <ColorMapEntry color="#0000FF" quantity="1" opacity="1" label="Sangat Rendah"/>
              <ColorMapEntry color="#00FFFF" quantity="2" opacity="1" label="Rendah"/>
              <ColorMapEntry color="#00FF00" quantity="3" opacity="1" label="Sedang"/>
              <ColorMapEntry color="#FFFF00" quantity="4" opacity="1" label="Tinggi"/>
              <ColorMapEntry color="#FF0000" quantity="5" opacity="1" label="Sangat Tinggi"/>
            </ColorMap>
          </RasterSymbolizer>
        </Rule>
      </FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>