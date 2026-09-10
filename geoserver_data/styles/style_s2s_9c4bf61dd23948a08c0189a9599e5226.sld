<?xml version="1.0" encoding="UTF-8"?><sld:StyledLayerDescriptor xmlns:sld="http://www.opengis.net/sld" xmlns="http://www.opengis.net/sld" xmlns:gml="http://www.opengis.net/gml" xmlns:ogc="http://www.opengis.net/ogc" version="1.0.0">
  <sld:NamedLayer>
    <sld:Name>Default Styler</sld:Name>
    <sld:UserStyle>
      <sld:Name>Default Styler</sld:Name>
      <sld:Title>Vegetation__NDVI Style</sld:Title>
      <sld:FeatureTypeStyle>
        <sld:Name>name</sld:Name>
        <sld:Rule>
          <sld:RasterSymbolizer>
            <sld:ColorMap>
              <sld:ColorMapEntry color="#0000FF" opacity="0.8" quantity="-1.0" label="Air (di bawah 0)"/>
              <sld:ColorMapEntry color="#D2B48C" opacity="0.85" quantity="0.0" label="Tanah Terbuka (0.0)"/>
              <sld:ColorMapEntry color="#FFFDD0" opacity="0.85" quantity="0.2" label="Non-Vegetasi (0.0 - 0.2)"/>
              <sld:ColorMapEntry color="#90EE90" opacity="0.9" quantity="0.5" label="Vegetasi Sedang (0.2 - 0.5)"/>
              <sld:ColorMapEntry color="#008000" opacity="0.95" quantity="0.8" label="Vegetasi Lebat (0.5 ke atas)"/>
            </sld:ColorMap>
            <sld:ContrastEnhancement/>
          </sld:RasterSymbolizer>
        </sld:Rule>
      </sld:FeatureTypeStyle>
    </sld:UserStyle>
  </sld:NamedLayer>
</sld:StyledLayerDescriptor>

