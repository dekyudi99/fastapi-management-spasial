<?xml version="1.0" encoding="UTF-8"?><sld:StyledLayerDescriptor xmlns:sld="http://www.opengis.net/sld" xmlns="http://www.opengis.net/sld" xmlns:gml="http://www.opengis.net/gml" xmlns:ogc="http://www.opengis.net/ogc" version="1.0.0">
  <sld:NamedLayer>
    <sld:Name>Default Styler</sld:Name>
    <sld:UserStyle>
      <sld:Name>Default Styler</sld:Name>
      <sld:Title>Wetness__NDWI Style</sld:Title>
      <sld:FeatureTypeStyle>
        <sld:Name>name</sld:Name>
        <sld:Rule>
          <sld:RasterSymbolizer>
            <sld:ColorMap>
              <sld:ColorMapEntry color="#D73027" opacity="0.85" quantity="-1.0" label="Lahan Kering (di bawah -0.2)"/>
              <sld:ColorMapEntry color="#FC8D59" opacity="0.85" quantity="-0.2" label="Kelembapan Rendah (-0.2 - 0.0)"/>
              <sld:ColorMapEntry color="#FEE08B" opacity="0.85" quantity="0.0" label="Netral (0.0)"/>
              <sld:ColorMapEntry color="#91BFDB" opacity="0.9" quantity="0.3" label="Lembab / Basah (0.1 - 0.3)"/>
              <sld:ColorMapEntry color="#4575B4" opacity="0.95" quantity="1.0" label="Badan Air (0.3 ke atas)"/>
            </sld:ColorMap>
            <sld:ContrastEnhancement/>
          </sld:RasterSymbolizer>
        </sld:Rule>
      </sld:FeatureTypeStyle>
    </sld:UserStyle>
  </sld:NamedLayer>
</sld:StyledLayerDescriptor>

