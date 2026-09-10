<?xml version="1.0" encoding="UTF-8"?><sld:StyledLayerDescriptor xmlns:sld="http://www.opengis.net/sld" xmlns="http://www.opengis.net/sld" xmlns:gml="http://www.opengis.net/gml" xmlns:ogc="http://www.opengis.net/ogc" version="1.0.0">
  <sld:NamedLayer>
    <sld:Name>Default Styler</sld:Name>
    <sld:UserStyle>
      <sld:Name>Default Styler</sld:Name>
      <sld:Title>Elevation Style</sld:Title>
      <sld:FeatureTypeStyle>
        <sld:Name>name</sld:Name>
        <sld:Rule>
          <sld:RasterSymbolizer>
            <sld:ColorMap>
              <sld:ColorMapEntry color="#008000" opacity="0.9" quantity="0" label="0m (Lowland)"/>
              <sld:ColorMapEntry color="#7CFC00" opacity="0.9" quantity="25" label="25m"/>
              <sld:ColorMapEntry color="#FFFF00" opacity="0.9" quantity="50" label="50m (Moderate)"/>
              <sld:ColorMapEntry color="#FF8C00" opacity="0.9" quantity="75" label="75m"/>
              <sld:ColorMapEntry color="#FF0000" opacity="0.9" quantity="100" label="100m (Highland)"/>
              <sld:ColorMapEntry color="#FFFFFF" opacity="0.9" quantity="200" label="&gt; 100m"/>
            </sld:ColorMap>
            <sld:ContrastEnhancement/>
          </sld:RasterSymbolizer>
        </sld:Rule>
      </sld:FeatureTypeStyle>
    </sld:UserStyle>
  </sld:NamedLayer>
</sld:StyledLayerDescriptor>

