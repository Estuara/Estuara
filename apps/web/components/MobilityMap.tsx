"use client";

import maplibregl, { GeoJSONSource, LngLatBoundsLike, Map, StyleSpecification } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import type { FeatureCollection } from "geojson";
import type { MobilityAnalysis } from "@/lib/types";

type Props = {
  analysis: MobilityAnalysis | null;
  selectedTrip: string | null;
  onSelectTrip: (tripId: string) => void;
};

// OpenFreeMap: OpenStreetMap vector tiles with water polygons (lagoon and
// creek coastlines), no API key and no usage cap. See docs/DEPLOYMENT.md.
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";

const FALLBACK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "water", type: "background", paint: { "background-color": "#d9e9e8" } }],
};

const ROUTE_LAYERS = ["routes-hit", "routes"];

export default function MobilityMap({ analysis, selectedTrip, onSelectTrip }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const fittedAnalysis = useRef<string | null>(null);
  const selectRef = useRef(onSelectTrip);
  const [styleRevision, setStyleRevision] = useState(0);
  const [basemapFailed, setBasemapFailed] = useState(false);

  useEffect(() => {
    selectRef.current = onSelectTrip;
  }, [onSelectTrip]);

  useEffect(() => {
    if (!container.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: BASEMAP_STYLE,
      center: [3.41, 6.455],
      zoom: 12,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

    let fellBack = false;
    map.on("error", () => {
      // Fall back to a plain water background if the basemap style cannot
      // load (offline, blocked tile host). Later tile errors are ignored.
      // Analysis layers are re-added on the fallback's style.load.
      if (!fellBack && !map.isStyleLoaded()) {
        fellBack = true;
        setBasemapFailed(true);
        map.setStyle(FALLBACK_STYLE);
      }
    });
    map.on("style.load", () => setStyleRevision((revision) => revision + 1));

    for (const layer of ROUTE_LAYERS) {
      map.on("click", layer, (event) => {
        const tripId = event.features?.[0]?.properties?.trip_id;
        if (typeof tripId === "string") selectRef.current(tripId);
      });
      map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
    }
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !analysis || !map.isStyleLoaded()) return;

    const routeData: FeatureCollection = {
      ...analysis.routes_geojson,
      features: analysis.routes_geojson.features.map((feature) => ({
        ...feature,
        properties: {
          ...feature.properties,
          selected: !selectedTrip || feature.properties?.trip_id === selectedTrip,
        },
      })),
    };
    const stopData: FeatureCollection = {
      ...analysis.stops_geojson,
      features: analysis.stops_geojson.features.map((feature) => ({
        ...feature,
        properties: { ...feature.properties, selected: feature.properties?.trip_id === selectedTrip },
      })),
    };

    const upsert = (id: string, data: FeatureCollection) => {
      const source = map.getSource(id) as GeoJSONSource | undefined;
      if (source) source.setData(data);
      else map.addSource(id, { type: "geojson", data });
    };
    upsert("routes", routeData);
    upsert("stops", stopData);
    upsert("landing-points", analysis.landing_points_geojson);

    if (!map.getLayer("routes")) {
      map.addLayer({ id: "route-halo", type: "line", source: "routes", paint: { "line-color": "#ffffff", "line-width": 8, "line-opacity": 0.75 } });
      map.addLayer({
        id: "routes",
        type: "line",
        source: "routes",
        paint: {
          "line-color": ["case", ["get", "selected"], "#087f78", "#6fa8a3"],
          "line-width": ["case", ["get", "selected"], 5, 2],
          "line-opacity": ["case", ["get", "selected"], 1, 0.4],
        },
      });
      // Wide transparent line so thin routes are easy to click.
      map.addLayer({ id: "routes-hit", type: "line", source: "routes", paint: { "line-color": "#000000", "line-width": 16, "line-opacity": 0 } });
      map.addLayer({
        id: "stops",
        type: "circle",
        source: "stops",
        paint: {
          "circle-radius": ["case", ["get", "selected"], 8, 5],
          "circle-color": "#ffb000",
          "circle-opacity": ["case", ["get", "selected"], 1, 0.65],
          "circle-stroke-width": ["case", ["get", "selected"], 3, 1.5],
          "circle-stroke-color": "#fffaf0",
        },
      });
      map.addLayer({ id: "landing-points-halo", type: "circle", source: "landing-points", paint: { "circle-radius": 15, "circle-color": "#7c5cff", "circle-opacity": 0.18 } });
      map.addLayer({
        id: "landing-points",
        type: "circle",
        source: "landing-points",
        paint: { "circle-radius": 9, "circle-color": "#7c5cff", "circle-stroke-width": 3, "circle-stroke-color": "#ffffff" },
      });
    }

    // Fit to the data once per analysis, not on every trip selection.
    if (fittedAnalysis.current !== analysis.provenance.processed_at) {
      fittedAnalysis.current = analysis.provenance.processed_at;
      const bounds = new maplibregl.LngLatBounds();
      analysis.routes_geojson.features.forEach((feature) =>
        feature.geometry.coordinates.forEach((coordinate) => bounds.extend(coordinate as [number, number])),
      );
      if (!bounds.isEmpty()) map.fitBounds(bounds as LngLatBoundsLike, { padding: 70, maxZoom: 14, duration: 0 });
    }
  }, [analysis, selectedTrip, styleRevision]);

  return (
    <div className="map-shell">
      <div ref={container} className="map-canvas" aria-label="Analysed ferry routes map. Select a route to inspect its trip." />
      <div className="map-label top-left">
        <span className="live-dot" /> MOBILITY EVIDENCE
      </div>
      <div className="map-label bottom-left">
        <strong>Lagos waterways pilot</strong>
        <span>{basemapFailed ? "Basemap unavailable · route geometry only" : "Select a route to inspect its trip"}</span>
      </div>
    </div>
  );
}
