<?xml version="1.0" encoding="UTF-8"?><sld:StyledLayerDescriptor xmlns:sld="http://www.opengis.net/sld" xmlns="http://www.opengis.net/sld" xmlns:gml="http://www.opengis.net/gml" xmlns:ogc="http://www.opengis.net/ogc" version="1.0.0">
  <sld:NamedLayer>
    <sld:Name>Default Styler</sld:Name>
    <sld:UserStyle>
      <sld:Name>Default Styler</sld:Name>
      <sld:Title>Rainfall Style</sld:Title>
      <sld:FeatureTypeStyle>
        <sld:Name>name</sld:Name>
        <sld:Rule>
          <sld:RasterSymbolizer>
            <sld:ColorMap>
              <sld:ColorMapEntry color="#001137" opacity="1.0" quantity="0" label="0 - 2 mm"/>
              <sld:ColorMapEntry color="#0aab1e" opacity="1.0" quantity="2" label="2 - 4 mm"/>
              <sld:ColorMapEntry color="#e7eb05" opacity="1.0" quantity="4" label="4 - 6 mm"/>
              <sld:ColorMapEntry color="#ff4a2d" opacity="1.0" quantity="6" label="6 - 8 mm"/>
              <sld:ColorMapEntry color="#e90000" opacity="1.0" quantity="8" label="8+ mm"/>
            </sld:ColorMap>
            <sld:ContrastEnhancement/>
          </sld:RasterSymbolizer>
        </sld:Rule>
      </sld:FeatureTypeStyle>
    </sld:UserStyle>
  </sld:NamedLayer>
</sld:StyledLayerDescriptor>

