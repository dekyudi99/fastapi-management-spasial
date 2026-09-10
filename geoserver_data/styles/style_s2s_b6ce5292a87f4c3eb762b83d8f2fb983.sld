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
              <sld:ColorMapEntry color="#FFFFFF" opacity="0.2" quantity="0" label="0 mm (Dry)"/>
              <sld:ColorMapEntry color="#00BFFF" opacity="0.8" quantity="50" label="50 mm"/>
              <sld:ColorMapEntry color="#0000FF" opacity="0.85" quantity="100" label="100 mm"/>
              <sld:ColorMapEntry color="#00008B" opacity="0.9" quantity="200" label="200 mm"/>
              <sld:ColorMapEntry color="#800080" opacity="1.0" quantity="300" label="300+ mm (Extreme)"/>
            </sld:ColorMap>
            <sld:ContrastEnhancement/>
          </sld:RasterSymbolizer>
        </sld:Rule>
      </sld:FeatureTypeStyle>
    </sld:UserStyle>
  </sld:NamedLayer>
</sld:StyledLayerDescriptor>

