/**
 * The map (TT-28: MapLibre GL through react-map-gl). The style comes from
 * `VITE_MAP_STYLE_URL` (D13; the provider is open question 13): the free
 * MapLibre demo style is the development default only. Points are drawn with
 * the status map's presets, so a point's color means what the same status
 * means everywhere else.
 */
import "maplibre-gl/dist/maplibre-gl.css";

import { Badge, Tag } from "antd";
import { setWorkerUrl } from "maplibre-gl";
// Vite bundles MapLibre's web worker and gives its URL. MapLibre would
// otherwise look for the worker file next to its own module, which Vite's
// dependency pre-bundling (and the production build) moves.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useCallback, useEffect, useMemo, useRef } from "react";
import MapGL, {
  type MapRef,
  Marker,
  NavigationControl,
} from "react-map-gl/maplibre";

import type { StatusPreset } from "./status";

/** The style used when `VITE_MAP_STYLE_URL` is not set (development only, D13). */
const DEMO_STYLE_URL = "https://demotiles.maplibre.org/style.json";

const configuredStyleUrl: unknown = import.meta.env.VITE_MAP_STYLE_URL;

/** The map style in use. */
const MAP_STYLE_URL =
  typeof configuredStyleUrl === "string" && configuredStyleUrl !== ""
    ? configuredStyleUrl
    : DEMO_STYLE_URL;

setWorkerUrl(maplibreWorkerUrl);

/** Vietnam as a whole, shown before any point is known. */
const INITIAL_VIEW = { latitude: 16.0, longitude: 106.0, zoom: 4.8 };

/** Zoom used when the map centers on one selected point. */
const SELECTED_POINT_ZOOM = 13;

/**
 * One point on the map.
 *
 * Attributes:
 *   id: Identifies the point (a vehicle ID); passed back on click.
 *   latitude, longitude: WGS 84 position.
 *   preset: The status preset of the point's state.
 *   label: Short text shown on the selected point (a plate).
 */
export interface MapPoint {
  id: string;
  latitude: number;
  longitude: number;
  preset: StatusPreset;
  label: string;
}

/**
 * Props of `MapView`.
 *
 * Attributes:
 *   points: What to draw; the map fits them when the set of IDs changes.
 *   selectedId: The highlighted point; the map flies to it.
 *   onSelect: Called with a point's ID when it is clicked.
 */
interface MapViewProps {
  points: MapPoint[];
  selectedId: string | undefined;
  onSelect: (id: string) => void;
}

/** A MapLibre map with status-colored points. */
export function MapView({ points, selectedId, onSelect }: MapViewProps) {
  const mapRef = useRef<MapRef>(null);
  const pointIdsKey = useMemo(
    () =>
      points
        .map((point) => point.id)
        .sort()
        .join(","),
    [points],
  );

  // The IDs the view was last fitted to. The view is fitted when the set of
  // points changes (another fleet), not on every poll: a moving truck must not
  // keep resetting the user's zoom.
  const fittedIdsKeyRef = useRef<string | null>(null);

  const fitToPoints = useCallback(() => {
    const map = mapRef.current;
    if (
      map === null ||
      points.length === 0 ||
      fittedIdsKeyRef.current === pointIdsKey
    ) {
      return;
    }
    const longitudes = points.map((point) => point.longitude);
    const latitudes = points.map((point) => point.latitude);
    map.fitBounds(
      [
        [Math.min(...longitudes), Math.min(...latitudes)],
        [Math.max(...longitudes), Math.max(...latitudes)],
      ],
      { padding: 64, maxZoom: SELECTED_POINT_ZOOM, duration: 0 },
    );
    fittedIdsKeyRef.current = pointIdsKey;
  }, [points, pointIdsKey]);

  // react-map-gl creates the MapLibre map asynchronously, so the first fit
  // happens in `onLoad`; later fits (new points) happen here.
  useEffect(fitToPoints, [fitToPoints]);

  useEffect(() => {
    const selectedPoint = points.find((point) => point.id === selectedId);
    if (selectedPoint !== undefined) {
      mapRef.current?.flyTo({
        center: [selectedPoint.longitude, selectedPoint.latitude],
        zoom: SELECTED_POINT_ZOOM,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fly only when the selection changes
  }, [selectedId]);

  return (
    <MapGL
      ref={mapRef}
      initialViewState={INITIAL_VIEW}
      mapStyle={MAP_STYLE_URL}
      style={{ width: "100%", height: "100%" }}
      attributionControl={{ compact: true }}
      onLoad={fitToPoints}
    >
      <NavigationControl position="top-right" showCompass={false} />
      {points.map((point) => (
        <Marker
          key={point.id}
          latitude={point.latitude}
          longitude={point.longitude}
          anchor="bottom"
          onClick={(event) => {
            event.originalEvent.stopPropagation();
            onSelect(point.id);
          }}
        >
          {point.id === selectedId ? (
            <Tag color={point.preset}>{point.label}</Tag>
          ) : (
            <Badge status={point.preset} />
          )}
        </Marker>
      ))}
    </MapGL>
  );
}
