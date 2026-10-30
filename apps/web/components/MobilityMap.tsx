"use client";

import maplibregl, { GeoJSONSource, LngLatBoundsLike, Map } from "maplibre-gl";
import { useEffect, useRef } from "react";
import type { FeatureCollection } from "geojson";
import type { MobilityAnalysis } from "@/lib/types";

type Props = { analysis: MobilityAnalysis | null; selectedTrip: string | null };

const EMPTY_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "water", type: "background", paint: { "background-color": "#d9e9e8" } }],
};

export default function MobilityMap({ analysis, selectedTrip }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);

  useEffect(() => {
    if (!container.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: EMPTY_STYLE,
      center: [3.982, 6.579],
      zoom: 11.5,
      attributionControl: false,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !analysis) return;
    const sync = () => {
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
      if (!map.getSource("routes")) {
        map.addSource("routes", { type: "geojson", data: routeData });
        map.addLayer({
          id: "route-halo",
          type: "line",
          source: "routes",
          paint: { "line-color": "#ffffff", "line-width": 8, "line-opacity": 0.75 },
        });
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
      } else {
        (map.getSource("routes") as GeoJSONSource).setData(routeData);
      }
      if (!map.getSource("stops")) {
        map.addSource("stops", { type: "geojson", data: analysis.stops_geojson });
        map.addLayer({
          id: "stops",
          type: "circle",
          source: "stops",
          paint: {
            "circle-radius": 7,
            "circle-color": "#ffb000",
            "circle-stroke-width": 3,
            "circle-stroke-color": "#fffaf0",
          },
        });
      } else {
        (map.getSource("stops") as GeoJSONSource).setData(analysis.stops_geojson);
      }
      if (!map.getSource("landing-points")) {
        map.addSource("landing-points", { type: "geojson", data: analysis.landing_points_geojson });
        map.addLayer({
          id: "landing-points-halo",
          type: "circle",
          source: "landing-points",
          paint: { "circle-radius": 15, "circle-color": "#7c5cff", "circle-opacity": 0.18 },
        });
        map.addLayer({
          id: "landing-points",
          type: "circle",
          source: "landing-points",
          paint: {
            "circle-radius": 9,
            "circle-color": "#7c5cff",
            "circle-stroke-width": 3,
            "circle-stroke-color": "#ffffff",
          },
        });
      } else {
        (map.getSource("landing-points") as GeoJSONSource).setData(analysis.landing_points_geojson);
      }

      const bounds = new maplibregl.LngLatBounds();
      analysis.routes_geojson.features.forEach((feature) =>
        feature.geometry.coordinates.forEach((coordinate) =>
          bounds.extend(coordinate as [number, number]),
        ),
      );
      if (!bounds.isEmpty()) map.fitBounds(bounds as LngLatBoundsLike, { padding: 70, maxZoom: 14 });
    };
    if (map.isStyleLoaded()) sync();
    else map.once("load", sync);
  }, [analysis, selectedTrip]);

  return (
    <div className="map-shell">
      <div ref={container} className="map-canvas" aria-label="Analysed ferry routes map" />
      <div className="map-label top-left">
        <span className="live-dot" /> MOBILITY EVIDENCE
      </div>
      <div className="map-label bottom-left">
        <strong>Lagos waterways pilot</strong>
        <span>Route geometry from uploaded observations</span>
      </div>
    </div>
  );
}
