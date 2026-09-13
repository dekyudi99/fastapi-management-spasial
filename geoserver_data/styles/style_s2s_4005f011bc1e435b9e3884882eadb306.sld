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
              <sld:ColorMapEntry color="#E0F3F8" opacity="0.7" quantity="0" label="0 mm (Kering)"/>
              <sld:ColorMapEntry color="#67A9CF" opacity="0.75" quantity="10" label="10 mm (Rendah)"/>
              <sld:ColorMapEntry color="#1C9099" opacity="0.8" quantity="30" label="30 mm (Sedang)"/>
              <sld:ColorMapEntry color="#016C59" opacity="0.85" quantity="60" label="60 mm (Tinggi)"/>
              <sld:ColorMapEntry color="#014636" opacity="0.95" quantity="100" label="100+ mm (Sangat Tinggi)"/>
            </sld:ColorMap>
            <sld:ContrastEnhancement/>
          </sld:RasterSymbolizer>
        </sld:Rule>
      </sld:FeatureTypeStyle>
    </sld:UserStyle>
  </sld:NamedLayer>
</sld:StyledLayerDescriptor>

