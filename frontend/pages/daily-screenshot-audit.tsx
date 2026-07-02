'use client';

import PageHeader from '../components/PageHeader';
import { useState, useEffect } from 'react';
import { format } from 'date-fns';
import { ProtectedRoute } from '../components/ProtectedRoute';

// ============ TYPE DEFINITIONS ============
interface LineItem {
  label: string;
  deliveredPackages: number | null;
  completedRoutes: number | null;
}

interface ParsedSnapshot {
  detectedDate: string | null;
  completedRoutes: number | null;
  completedRoutesEstimated: boolean;
  deliveredPackages: number | null;
  deliveredPackagesEstimatedFromFractions: boolean;
  dspLateCancelMax: number | null;
  dspLateCancelCountAboveZero: number;
  lineItems: LineItem[];
  rawText: string;
}

interface SnapshotState {
  imageDataUrl: string | null;
  parsed: ParsedSnapshot | null;
}

interface VarianceResponse {
  metric: 'completedRoutes' | 'deliveredPackages';
  justification: string;
  disputeStatus: 'acknowledged' | 'dispute_submitted' | null;
}

type CaptureType = 'cortex' | 'wst';

// ============ UTILITY FUNCTIONS ============

function extractExcludedServices(rawText: string): string[] {
  const lines = rawText.split(/\r?\n/);
  const startIdx = lines.findIndex(line => /excluded services/i.test(line));
  if (startIdx === -1) return [];
  const result: string[] = [];
  for (let i = startIdx + 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line || /^[-=]+$/.test(line) || /^[A-Z ]{5,}$/.test(line)) break;
    result.push(line);
  }
  return result;
}

const normalizeSpaces = (value: string): string => value.replace(/\s+/g, ' ').trim();

const normalizeLabel = (value: string): string =>
  normalizeSpaces(value)
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, ' ')
    .trim();

const parseNumber = (value: string | undefined): number | null => {
  if (!value) return null;
  const digits = value.replace(/,/g, '').trim();
  if (!digits) return null;
  const parsed = Number.parseInt(digits, 10);
  return Number.isFinite(parsed) ? parsed : null;
};

const extractLineItems = (rawText: string): LineItem[] => {
  const normalized = rawText.replace(/\r/g, '\n');
  // WST format: Label | Packages | Routes (each on separate row)
  // Matches: label (text) | number (packages) | number (routes)
  const wstPattern = /([A-Za-z0-9:&()\/\-\s]{3,110}?)\s*\|\s*(\d{1,3}(?:,\d{3})*)\s*\|\s*(\d{1,3}(?:,\d{3})*)/gi;
  const cortexPattern = /(CX\d+|DLV\d+)\s+[\d\w]+[\s\S]{0,200}?(\d{1,3})\s*\/\s*(\d{1,3})\s+stops[\s\S]{0,120}?(\d{1,3})\s*\/\s*(\d{1,3})\s+deliveries/gi;
  
  const deduped = new Map<string, LineItem>();
  
  // Try WST format first
  let match: RegExpExecArray | null;
  while ((match = wstPattern.exec(normalized)) !== null) {
    const label = normalizeSpaces(match[1]);
    // Skip header rows (Label, Packages, Routes, etc.)
    if (/^(label|packages|routes|metric|cortex|wst|variance)$/i.test(label)) continue;
    
    const key = normalizeLabel(label);
    if (!key || key.length < 3) continue;
    
    const existing = deduped.get(key);
    const deliveredPackages = parseNumber(match[2]);
    const completedRoutes = parseNumber(match[3]);
    
    if (!existing) {
      deduped.set(key, { label, deliveredPackages, completedRoutes });
    } else {
      deduped.set(key, {
        label: existing.label.length >= label.length ? existing.label : label,
        deliveredPackages: existing.deliveredPackages ?? deliveredPackages,
        completedRoutes: existing.completedRoutes ?? completedRoutes,
      });
    }
  }
  
  // Also try Cortex format (routes with stops/deliveries)
  while ((match = cortexPattern.exec(normalized)) !== null) {
    const label = match[1];
    // Skip RDM routes (satellite/special routes that shouldn't be counted)
    if (/^RDM_/i.test(label)) continue;
    
    const key = normalizeLabel(label);
    if (!key || key.length < 2) continue;
    
    const stopsCompleted = parseInt(match[2], 10);
    const stopTotal = parseInt(match[3], 10);
    const deliveriesCompleted = parseInt(match[4], 10);
    const deliveriesTotal = parseInt(match[5], 10);
    
    if (!deduped.has(key)) {
      deduped.set(key, {
        label,
        deliveredPackages: deliveriesCompleted,
        completedRoutes: stopsCompleted,
      });
    }
  }
  
  return Array.from(deduped.values());
};

// --- Date Parsing Utilities ---
const monthMap: Record<string, number> = {
  jan: 1, january: 1, feb: 2, february: 2, mar: 3, march: 3, apr: 4, april: 4,
  may: 5, jun: 6, june: 6, jul: 7, july: 7, aug: 8, august: 8, sep: 9,
  sept: 9, september: 9, oct: 10, october: 10, nov: 11, november: 11,
  dec: 12, december: 12,
};

const dayNamePattern = '(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sun|Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat)';

const toIsoDate = (year: number, month: number, day: number): string | null => {
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  if (year < 2000 || month < 1 || month > 12 || day < 1 || day > 31) return null;
  
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    return null;
  }
  
  return `${year.toString().padStart(4, '0')}-${month.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`;
};

const findMetric = (text: string, patterns: RegExp[]): number | null => {
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match && match[1]) {
      return parseNumber(match[1]);
    }
  }
  return null;
};

const looksLikeCortexRouteDashboard = (rawText: string): boolean => {
  return /cortex|daily operations|route summary|trips|dsp/i.test(rawText);
};

const extractCompletedRoutesFromWstHeader = (rawText: string): number | null => {
  // Match: (pickup|delivered) packages [anything] number [anything] completed routes
  // Uses [\s\S]*? to match across newlines
  const match = rawText.match(/(?:pickup|delivered)\s+packages[\s\S]*?(\d{1,4})[\s\S]*?completed\s+routes/i);
  if (match && match[1]) {
    const num = parseNumber(match[1]);
    // Sanity check: routes should be < 1000
    if (num !== null && num < 1000) {
      return num;
    }
  }
  return null;
};

const extractTripsFromCortexHeader = (rawText: string): number | null => {
  let count = findMetric(rawText, [
    /routes?[\s:=]+(\d{1,3}(?:,\d{3})*)/i,
    /trips?[\s:=]+(\d{1,3}(?:,\d{3})*)/i,
    /total\s+routes?[\s:=]+(\d{1,3}(?:,\d{3})*)/i,
    /total\s+trips?[\s:=]+(\d{1,3}(?:,\d{3})*)/i,
  ]);
  
  // Count RDM routes and subtract them from the total
  if (count !== null) {
    const rdmMatches = Array.from(rawText.matchAll(/RDM_[A-Za-z0-9_\-=]+/gi));
    if (rdmMatches.length > 0) {
      count = count - rdmMatches.length;
    }
  }
  
  return count;
};

const estimateCompletedRoutesFromRouteFractions = (rawText: string): number | null => {
  const matches = Array.from(rawText.matchAll(/(\d+)\s*\/\s*(\d+)\s*routes?/gi));
  if (matches.length === 0) return null;
  // Only use the first match, don't sum them - take the highest denominator as truest count
  const sorted = matches.sort((a, b) => parseInt(b[2], 10) - parseInt(a[2], 10));
  const best = sorted[0];
  const completed = parseInt(best[1], 10);
  return completed > 0 ? completed : null;
};

const estimateDeliveredPackagesFromRouteFractions = (rawText: string): number | null => {
  // Try to find a "Delivered" total or "Total packages" number
  const patterns = [
    /total\s+packages?[\s:=]+(\d{1,3}(?:,\d{3})*)/i,
    /packages?\s+delivered[\s:=]+(\d{1,3}(?:,\d{3})*)/i,
    /(\d{1,3}(?:,\d{3})*)\s+packages?/i,
  ];
  
  for (const pattern of patterns) {
    const match = rawText.match(pattern);
    if (match && match[1]) {
      return parseNumber(match[1]);
    }
  }
  return null;
};

const sumCompletedDeliveries = (rawText: string): number | null => {
  // For Cortex format: sum the numerators of all "X/Y deliveries" fractions
  const matches = Array.from(rawText.matchAll(/(\d+)\s*\/\s*(\d+)\s*deliveries/gi));
  if (matches.length === 0) return null;
  const totalCompleted = matches.reduce((sum, m) => sum + parseInt(m[1], 10), 0);
  return totalCompleted > 0 ? totalCompleted : null;
};

const parseDetectedDate = (rawText: string): string | null => {
  const headerText = rawText.split(/\r?\n/).slice(0, 90).join('\n');
  const headerLines = headerText
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean);

  const weekRangePattern = /\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s*\d{1,2}\s*[-–—]\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s*\d{1,2}\b/i;
  const serviceLineWithYearPattern = new RegExp(
    String.raw`\b${dayNamePattern}\b\s+` +
      String.raw`(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+` +
      String.raw`(\d{1,2})(?:,)?\s+(\d{4})\b`,
    'i'
  );
  const standaloneMonthDayYearPattern =
    /\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(\d{1,2})(?:,)?\s+(\d{4})\b/i;

  for (const line of headerLines) {
    if (weekRangePattern.test(line)) continue;
    const serviceLineMatch = line.match(serviceLineWithYearPattern);
    if (serviceLineMatch) {
      const monthKey = serviceLineMatch[2].toLowerCase();
      const month = monthMap[monthKey];
      const day = Number.parseInt(serviceLineMatch[3], 10);
      const year = Number.parseInt(serviceLineMatch[4], 10);
      const iso = toIsoDate(year, month, day);
      if (iso) return iso;
    }
  }

  for (const line of headerLines) {
    if (weekRangePattern.test(line)) continue;
    const match = line.match(standaloneMonthDayYearPattern);
    if (match) {
      const monthKey = match[1].toLowerCase();
      const month = monthMap[monthKey];
      const day = Number.parseInt(match[2], 10);
      const year = Number.parseInt(match[3], 10);
      const iso = toIsoDate(year, month, day);
      if (iso) return iso;
    }
  }

  const lineLevelNumericPattern = /\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})\b/;
  for (const line of headerLines) {
    if (weekRangePattern.test(line)) continue;
    const numericMatch = line.match(lineLevelNumericPattern);
    if (!numericMatch) continue;
    const month = Number.parseInt(numericMatch[1], 10);
    const day = Number.parseInt(numericMatch[2], 10);
    const yearRaw = Number.parseInt(numericMatch[3], 10);
    const year = yearRaw < 100 ? 2000 + yearRaw : yearRaw;
    const iso = toIsoDate(year, month, day);
    if (iso) return iso;
  }

  const fallbackMonthNameMatch = rawText.match(standaloneMonthDayYearPattern);
  if (fallbackMonthNameMatch) {
    const monthKey = fallbackMonthNameMatch[1].toLowerCase();
    const month = monthMap[monthKey];
    const day = Number.parseInt(fallbackMonthNameMatch[2], 10);
    const year = Number.parseInt(fallbackMonthNameMatch[3], 10);
    const iso = toIsoDate(year, month, day);
    if (iso) return iso;
  }

  const fallbackNumericMatch = rawText.match(/\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})\b/);
  if (fallbackNumericMatch) {
    const month = Number.parseInt(fallbackNumericMatch[1], 10);
    const day = Number.parseInt(fallbackNumericMatch[2], 10);
    const yearRaw = Number.parseInt(fallbackNumericMatch[3], 10);
    const year = yearRaw < 100 ? 2000 + yearRaw : yearRaw;
    const iso = toIsoDate(year, month, day);
    if (iso) return iso;
  }

  return null;
};

const getHeaderText = (rawText: string): string =>
  rawText.split(/\r?\n/).slice(0, 90).join('\n');

const emptySnapshot: SnapshotState = {
  imageDataUrl: null,
  parsed: null,
};

const parseSnapshot = (rawText: string): ParsedSnapshot => {
  const detectedDate = parseDetectedDate(rawText);
  const headerText = getHeaderText(rawText);
  const lineItems = extractLineItems(rawText);
  const looksLikeCortex = looksLikeCortexRouteDashboard(rawText);
  const wstSummaryCompletedRoutes = extractCompletedRoutesFromWstHeader(rawText);
  const wstCompletedRoutesFromLineItems =
    !looksLikeCortex && lineItems.length > 0
      ? lineItems
          .filter((line) => !/\b(?:training|rdm_|unknown)\b/i.test(line.label))
          .reduce((sum, line) => sum + (line.completedRoutes ?? 0), 0)
      : null;
  const cortexTripsAsRoutes = looksLikeCortex ? extractTripsFromCortexHeader(rawText) : null;
  const cortexCompletedRoutesFromFractions = looksLikeCortex ? estimateCompletedRoutesFromRouteFractions(rawText) : null;

  const completedRoutesFromHeaderPatterns = findMetric(headerText, [
    /(\d{1,3}(?:,\d{3})*)\s*(?:completed\s*rout(?:e|es|cs)|rout(?:e|es|cs)\s*completed)/i,
    /(?:completed\s*rout(?:e|es|cs)|rout(?:e|es|cs)\s*completed|total\s*rout(?:e|es|cs))\D{0,16}(\d{1,3}(?:,\d{3})*)/i,
  ]);

  const completedRoutes = looksLikeCortex
    ? cortexTripsAsRoutes ?? completedRoutesFromHeaderPatterns
    : wstCompletedRoutesFromLineItems ?? wstSummaryCompletedRoutes ?? completedRoutesFromHeaderPatterns;

  const completedRoutesEstimated =
    looksLikeCortex
      ? completedRoutes !== null && (cortexTripsAsRoutes === null || completedRoutes === cortexCompletedRoutesFromFractions)
      : completedRoutes !== null && wstSummaryCompletedRoutes === null && wstCompletedRoutesFromLineItems !== null;

  const directDeliveredPackages = looksLikeCortex
    ? (sumCompletedDeliveries(rawText) ??
      findMetric(headerText, [
        /(\d{1,3}(?:,\d{3})*)\s*(?:delivered\s*packag(?:e|es|cs)|packag(?:e|es|cs)\s*delivered)/i,
        /(?:delivered\s*packag(?:e|es|cs)|total\s*packag(?:e|es|cs))\D{0,16}(\d{1,3}(?:,\d{3})*)/i,
      ]))
    : findMetric(headerText, [
      /(\d{1,3}(?:,\d{3})*)\s*(?:delivered\s*packag(?:e|es|cs)|packag(?:e|es|cs)\s*delivered)/i,
      /(?:delivered\s*packag(?:e|es|cs)|total\s*packag(?:e|es|cs))\D{0,16}(\d{1,3}(?:,\d{3})*)/i,
    ]) ??
    findMetric(rawText, [
      /(\d{1,3}(?:,\d{3})*)\s*\n\s*delivered\s*packages?/i,
      /(\d{1,3}(?:,\d{3})*)\s*\n\s*total\s*packages?/i,
      /delivered\s*packages?[\s:=\n]+(\d{1,3}(?:,\d{3})*)/i,
      /total\s*packages?[\s:=\n]+(\d{1,3}(?:,\d{3})*)/i,
      /(\d{1,3}(?:,\d{3})*)\s*(?:delivered\s*packag(?:e|es|cs)|packag(?:e|es|cs)\s*delivered)/i,
      /(?:delivered\s*packag(?:e|es|cs)|total\s*packag(?:e|es|cs))\D{0,20}(\d{1,3}(?:,\d{3})*)/i,
      /(\d{1,3}(?:,\d{3})*)\s*packag(?:e|es|cs)\b/i,
    ]);

  const estimatedDeliveredPackages = directDeliveredPackages === null 
    ? (lineItems.length > 0 
        ? lineItems
          .filter((line) => !/\b(?:training|rdm_|unknown)\b/i.test(line.label))
          .reduce((sum, line) => sum + (line.deliveredPackages ?? 0), 0) || null
        : estimateDeliveredPackagesFromRouteFractions(rawText))
    : null;

  const undeliverable = findMetric(rawText, [
    /(\d{1,3}(?:,\d{3})*)\s*undeliverable/i,
    /undeliverable\s*[:=]?\s*(\d{1,3}(?:,\d{3})*)/i,
  ]) ?? 0;

  const returnedToStation = findMetric(rawText, [
    /(\d{1,3}(?:,\d{3})*)\s*returned to station/i,
    /returned to station\s*[:=]?\s*(\d{1,3}(?:,\d{3})*)/i,
  ]) ?? 0;

  const pickupFailed = findMetric(rawText, [
    /(\d{1,3}(?:,\d{3})*)\s*pickup failed/i,
    /pickup failed\s*[:=]?\s*(\d{1,3}(?:,\d{3})*)/i,
  ]) ?? 0;

  const remaining = findMetric(rawText, [
    /(\d{1,3}(?:,\d{3})*)\s*remaining/i,
    /remaining\s*[:=]?\s*(\d{1,3}(?:,\d{3})*)/i,
  ]) ?? 0;

  const reattemptable = findMetric(rawText, [
    /(\d{1,3}(?:,\d{3})*)\s*reattemptable/i,
    /reattemptable\s*[:=]?\s*(\d{1,3}(?:,\d{3})*)/i,
  ]) ?? 0;

  const missing = findMetric(rawText, [
    /(\d{1,3}(?:,\d{3})*)\s*missing/i,
    /missing\s*[:=]?\s*(\d{1,3}(?:,\d{3})*)/i,
  ]) ?? 0;

  const pendingContainers = findMetric(rawText, [
    /(\d{1,3}(?:,\d{3})*)\s*pending containers? pickup/i,
    /pending containers? pickup\s*[:=]?\s*(\d{1,3}(?:,\d{3})*)/i,
  ]) ?? 0;

  let deliveredPackages = directDeliveredPackages ?? estimatedDeliveredPackages;
  if (deliveredPackages !== null) {
    deliveredPackages = deliveredPackages - undeliverable - returnedToStation - pickupFailed - remaining - reattemptable - missing - pendingContainers;
    if (deliveredPackages < 0) deliveredPackages = 0;
  }

  const deliveredPackagesEstimatedFromFractions = directDeliveredPackages === null && estimatedDeliveredPackages !== null;

  const dspLateCancelMatches = Array.from(rawText.matchAll(/(\d{1,3}(?:,\d{3})*)\s*DSP\s*late\s*cancel/gi));
  const dspLateCancelValues = dspLateCancelMatches
    .map((match) => parseNumber(match[1]))
    .filter((value): value is number => value !== null);
  const dspLateCancelMax = dspLateCancelValues.length > 0 ? Math.max(...dspLateCancelValues) : null;
  const dspLateCancelCountAboveZero = dspLateCancelValues.filter((value) => value > 0).length;

  return {
    detectedDate,
    completedRoutes,
    completedRoutesEstimated,
    deliveredPackages,
    deliveredPackagesEstimatedFromFractions,
    dspLateCancelMax,
    dspLateCancelCountAboveZero,
    lineItems,
    rawText,
  };
};

const mergeParsedSnapshots = (primary: ParsedSnapshot, secondary: ParsedSnapshot): ParsedSnapshot => ({
  detectedDate: primary.detectedDate ?? secondary.detectedDate,
  completedRoutes: primary.completedRoutes ?? secondary.completedRoutes,
  completedRoutesEstimated:
    primary.completedRoutes !== null ? primary.completedRoutesEstimated : secondary.completedRoutesEstimated,
  deliveredPackages: primary.deliveredPackages ?? secondary.deliveredPackages,
  deliveredPackagesEstimatedFromFractions:
    primary.deliveredPackages !== null
      ? primary.deliveredPackagesEstimatedFromFractions
      : secondary.deliveredPackagesEstimatedFromFractions,
  dspLateCancelMax: primary.dspLateCancelMax ?? secondary.dspLateCancelMax,
  dspLateCancelCountAboveZero:
    primary.dspLateCancelCountAboveZero > 0 || secondary.dspLateCancelCountAboveZero > 0
      ? Math.max(primary.dspLateCancelCountAboveZero, secondary.dspLateCancelCountAboveZero)
      : 0,
  lineItems: primary.lineItems.length >= secondary.lineItems.length ? primary.lineItems : secondary.lineItems,
  rawText:
    primary.rawText.length >= secondary.rawText.length
      ? `${primary.rawText}\n\n--- OCR PASS 2 ---\n\n${secondary.rawText}`
      : `${secondary.rawText}\n\n--- OCR PASS 1 ---\n\n${primary.rawText}`,
});

const formatValue = (value: number | null | undefined): string => (value === null || value === undefined ? 'N/A' : value.toLocaleString());

const fileToDataUrl = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        resolve(reader.result);
        return;
      }
      reject(new Error('Failed to read selected image file.'));
    };
    reader.onerror = () => reject(new Error('Failed to read selected image file.'));
    reader.readAsDataURL(file);
  });

// ============ MAIN COMPONENT ============

export default function DailyScreenshotAuditPage() {
  const [excludedServices, setExcludedServices] = useState<string[]>([]);
  const [excludedConfirm, setExcludedConfirm] = useState<Record<number, 'ack' | 'dispute' | null>>({});

  const [cortex, setCortex] = useState<SnapshotState>(emptySnapshot);
  const [wst, setWst] = useState<SnapshotState>(emptySnapshot);
  const [cortexPaste, setCortexPaste] = useState<string>('');
  const [wstPaste, setWstPaste] = useState<string>('');
  const [statusMessage, setStatusMessage] = useState<string>('');
  
  const todayIso = format(new Date(), 'yyyy-MM-dd');
  const [selectedDate, setSelectedDate] = useState<string>(todayIso);
  const [trainingDays, setTrainingDays] = useState<number>(0);
  const [auditLocked, setAuditLocked] = useState(false);

  // Variance tracking
  const [varianceResponses, setVarianceResponses] = useState<Record<string, VarianceResponse>>({});
  const [varianceModal, setVarianceModal] = useState<{
    isOpen: boolean;
    metric: 'completedRoutes' | 'deliveredPackages' | null;
    variance: number;
  }>({ isOpen: false, metric: null, variance: 0 });
  const [currentJustification, setCurrentJustification] = useState<string>('');
  const [currentDisputeStatus, setCurrentDisputeStatus] = useState<'acknowledged' | 'dispute_submitted' | null>(
    null
  );

  const handleCortexPaste = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = event.target.value;
    setCortexPaste(value);
    if (value.trim().length > 0) {
      setCortex({ imageDataUrl: null, parsed: parseSnapshot(value) });
      setStatusMessage('Cortex data pasted and parsed.');
    } else {
      setCortex(emptySnapshot);
    }
  };

  const handleWstPaste = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = event.target.value;
    setWstPaste(value);
    if (value.trim().length > 0) {
      setWst({ imageDataUrl: null, parsed: parseSnapshot(value) });
      setStatusMessage('WST data pasted and parsed.');
      const exclusions = extractExcludedServices(value);
      setExcludedServices(exclusions);
      setExcludedConfirm(Object.fromEntries(exclusions.map((_, i) => [i, null])));
    } else {
      setWst(emptySnapshot);
      setExcludedServices([]);
      setExcludedConfirm({});
    }
  };

  const openVarianceModal = (metric: 'completedRoutes' | 'deliveredPackages', variance: number) => {
    const existing = varianceResponses[metric];
    setCurrentJustification(existing?.justification || '');
    setCurrentDisputeStatus(existing?.disputeStatus || null);
    setVarianceModal({ isOpen: true, metric, variance });
  };

  const saveVarianceResponse = () => {
    if (!varianceModal.metric) return;
    if (!currentJustification.trim()) {
      setStatusMessage('Please enter a justification before proceeding.');
      return;
    }
    if (!currentDisputeStatus) {
      setStatusMessage('Please select whether this is acknowledged or a dispute.');
      return;
    }
    const metric = varianceModal.metric;
    setVarianceResponses(prev => ({
      ...prev,
      [metric]: {
        metric,
        justification: currentJustification,
        disputeStatus: currentDisputeStatus,
      },
    }));
    setVarianceModal({ isOpen: false, metric: null, variance: 0 });
    setCurrentJustification('');
    setCurrentDisputeStatus(null);
  };

  const hasUnrespondedVariances = (): boolean => {
    const metrics: Array<'completedRoutes' | 'deliveredPackages'> = ['completedRoutes', 'deliveredPackages'];
    for (const metric of metrics) {
      const cortexVal = metric === 'completedRoutes' ? cortex.parsed?.completedRoutes : cortex.parsed?.deliveredPackages;
      const wstVal = metric === 'completedRoutes' ? wst.parsed?.completedRoutes : wst.parsed?.deliveredPackages;
      if (cortexVal && wstVal && cortexVal !== wstVal && !varianceResponses[metric]) {
        return true;
      }
    }
    return false;
  };

  useEffect(() => {
    (async () => {
      try {
        const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
        const token = localStorage.getItem('access_token');
        const headers: Record<string, string> = {};
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }
        const resp = await fetch(`${API_URL}/audit/approved-audits?start_date=${selectedDate}&end_date=${selectedDate}`, {
          headers,
        });
        if (resp.ok) {
          const audits = await resp.json();
          setAuditLocked(Array.isArray(audits) && audits.length > 0);
        } else {
          setAuditLocked(false);
        }
      } catch {
        setAuditLocked(false);
      }
    })();
  }, [selectedDate]);

  const handleConfirmClick = async () => {
    if (!cortex.parsed || !wst.parsed) {
      setStatusMessage('Paste and parse both Cortex and WST data first.');
      return;
    }
    if (!selectedDate) {
      setStatusMessage('Please select the audit date.');
      return;
    }
    if (!trainingDays || Number(trainingDays) <= 0) {
      setStatusMessage('Please enter the number of training days.');
      return;
    }
    if (hasUnrespondedVariances()) {
      setStatusMessage('Please review and respond to all variances before submitting.');
      return;
    }

    setStatusMessage('Saving audit...');
    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
      const token = localStorage.getItem('access_token');
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
      const payload = {
        station: 'Daily Audit',
        audit_date: selectedDate,
        cortex_raw: cortex.parsed?.rawText || '',
        wst_raw: wst.parsed?.rawText || '',
        notes: `Training days: ${trainingDays}`,
        variance_responses: varianceResponses,
      };
      
      console.log('Cortex parsed data:', cortex.parsed);
      console.log('WST parsed data:', wst.parsed);
      console.log('Submitting audit payload:', payload);
      console.log('Cortex raw length:', payload.cortex_raw.length);
      console.log('WST raw length:', payload.wst_raw.length);
      console.log('Variance responses:', varianceResponses);
      
      const response = await fetch(`${API_URL}/audit/approved-audits`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      });

      console.log('Response status:', response.status);
      
      if (response.ok) {
        const responseData = await response.json();
        console.log('Audit saved:', responseData);
        setStatusMessage('Audit saved successfully!');
        setAuditLocked(true);
      } else {
        const errText = await response.text();
        console.error('Failed response:', errText);
        setStatusMessage(`Failed to save audit: ${response.status} - ${errText || 'Unknown error'}`);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setStatusMessage(`Error saving audit: ${message}`);
    }
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
        <PageHeader title="Daily Screenshot Audit" showBack />

        <main className="max-w-6xl mx-auto px-4 py-8 space-y-6">
          {/* --- Date Picker --- */}
          <div className="bg-white rounded-lg shadow-md p-6 mb-6">
            <label className="block text-sm text-gray-700 font-semibold mb-2">Audit Date</label>
            <input
              type="date"
              className="border rounded p-2 text-sm mb-4"
              value={selectedDate}
              max={todayIso}
              onChange={e => setSelectedDate(e.target.value)}
            />
            <label className="block text-sm text-gray-700 font-semibold mb-2">Training Days</label>
            <input
              type="number"
              min={0}
              className="border rounded p-2 text-sm mb-4"
              value={trainingDays}
              onChange={e => setTrainingDays(Number(e.target.value))}
            />
          </div>

          {/* --- Paste Input Section --- */}
          <div className="bg-white rounded-lg shadow-md p-6 mb-6">
            <h2 className="text-xl font-bold text-gray-900 mb-2">Paste Data</h2>
            <p className="text-sm text-gray-700 mb-4">
              Copy the full table or summary text from Cortex and WST, then paste below.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="border rounded-md p-4 space-y-3">
                <label className="block text-sm text-gray-700 font-semibold mb-1">Paste Cortex Data</label>
                <textarea
                  className="w-full border rounded p-2 text-sm font-mono"
                  rows={8}
                  placeholder="Paste Cortex table or summary text here..."
                  value={cortexPaste}
                  onChange={handleCortexPaste}
                />
              </div>
              <div className="border rounded-md p-4 space-y-3">
                <label className="block text-sm text-gray-700 font-semibold mb-1">Paste WST Data</label>
                <textarea
                  className="w-full border rounded p-2 text-sm font-mono"
                  rows={8}
                  placeholder="Paste WST table or summary text here..."
                  value={wstPaste}
                  onChange={handleWstPaste}
                />
              </div>
            </div>

            <div className="mt-6 flex justify-between items-center">
              <button
                className="px-6 py-2 bg-gray-400 text-white rounded-md font-semibold hover:bg-gray-500"
                type="button"
                onClick={() => {
                  setCortexPaste('');
                  setWstPaste('');
                  setCortex(emptySnapshot);
                  setWst(emptySnapshot);
                  setStatusMessage('');
                  setTrainingDays(0);
                }}
              >
                Reset
              </button>
              <button
                className="px-6 py-2 bg-green-600 text-white rounded-md font-semibold hover:bg-green-700 disabled:bg-gray-400"
                disabled={
                  cortexPaste.trim().length === 0 ||
                  wstPaste.trim().length === 0 ||
                  !selectedDate ||
                  !trainingDays ||
                  Number(trainingDays) <= 0 ||
                  (excludedServices.length > 0 && excludedServices.some((_, i) => !excludedConfirm[i]))
                }
                onClick={handleConfirmClick}
              >
                Confirm
              </button>
            </div>
          </div>

          {/* --- Excluded Services Confirmation --- */}
          {excludedServices.length > 0 && (
            <div className="bg-yellow-50 border-l-4 border-yellow-400 rounded-md p-4 mb-6">
              <h3 className="font-semibold text-yellow-800 mb-2">Excluded Services</h3>
              <p className="text-sm text-yellow-700 mb-2">Please review each excluded service below.</p>
              <ul className="space-y-2">
                {excludedServices.map((item, idx) => (
                  <li key={idx} className="flex items-center space-x-3">
                    <span className="flex-1 text-gray-800">{item}</span>
                    <button
                      className={`px-2 py-1 rounded text-xs font-semibold ${
                        excludedConfirm[idx] === 'ack' ? 'bg-green-600 text-white' : 'bg-gray-200 text-gray-700'
                      }`}
                      onClick={() => setExcludedConfirm(c => ({ ...c, [idx]: 'ack' }))}
                      type="button"
                    >
                      Acknowledge
                    </button>
                    <button
                      className={`px-2 py-1 rounded text-xs font-semibold ${
                        excludedConfirm[idx] === 'dispute' ? 'bg-red-600 text-white' : 'bg-gray-200 text-gray-700'
                      }`}
                      onClick={() => setExcludedConfirm(c => ({ ...c, [idx]: 'dispute' }))}
                      type="button"
                    >
                      Dispute Entered
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* --- Status Message --- */}
          {statusMessage && (
            <div className="bg-blue-50 border-l-4 border-blue-400 rounded-md p-4">
              <p className="text-sm text-blue-800">{statusMessage}</p>
            </div>
          )}

          {/* --- Audit Results Matrix --- */}
          {(cortex.parsed || wst.parsed) && (
            <div className="bg-white rounded-lg shadow-md p-6 mb-6">
              <h2 className="text-xl font-bold text-gray-900 mb-4">Audit Results</h2>
              
              {/* Detected Dates */}
              {(cortex.parsed?.detectedDate || wst.parsed?.detectedDate) && (
                <div className="mb-6">
                  <h3 className="text-sm font-semibold text-gray-700 mb-2">Detected Dates</h3>
                  <div className="grid grid-cols-3 gap-4">
                    <div className="border rounded p-3 bg-gray-50">
                      <p className="text-xs text-gray-600">Cortex Date</p>
                      <p className="text-sm font-semibold text-gray-900">{cortex.parsed?.detectedDate || 'N/A'}</p>
                    </div>
                    <div className="border rounded p-3 bg-gray-50">
                      <p className="text-xs text-gray-600">WST Date</p>
                      <p className="text-sm font-semibold text-gray-900">{wst.parsed?.detectedDate || 'N/A'}</p>
                    </div>
                    <div className="border rounded p-3 bg-blue-50">
                      <p className="text-xs text-gray-600">Audit Date</p>
                      <p className="text-sm font-semibold text-gray-900">{selectedDate}</p>
                    </div>
                  </div>
                </div>
              )}

              {/* Key Metrics */}
              <div className="mb-6">
                <h3 className="text-sm font-semibold text-gray-700 mb-2">Key Metrics</h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm border-collapse">
                    <thead>
                      <tr className="bg-gray-200">
                        <th className="border p-2 text-left font-semibold">Metric</th>
                        <th className="border p-2 text-center font-semibold">Cortex</th>
                        <th className="border p-2 text-center font-semibold">WST</th>
                        <th className="border p-2 text-center font-semibold">Variance</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td className="border p-2 font-semibold">Completed Routes</td>
                        <td className="border p-2 text-center">{formatValue(cortex.parsed?.completedRoutes)}</td>
                        <td className="border p-2 text-center">{formatValue(wst.parsed?.completedRoutes)}</td>
                        <td className="border p-2 text-center cursor-pointer hover:bg-blue-100"
                          onClick={() => {
                            if (cortex.parsed?.completedRoutes && wst.parsed?.completedRoutes && 
                                cortex.parsed.completedRoutes !== wst.parsed.completedRoutes) {
                              openVarianceModal('completedRoutes', Math.abs(cortex.parsed.completedRoutes - wst.parsed.completedRoutes));
                            }
                          }}
                        >
                          {cortex.parsed?.completedRoutes && wst.parsed?.completedRoutes
                            ? Math.abs(cortex.parsed.completedRoutes - wst.parsed.completedRoutes)
                            : 'N/A'}
                        </td>
                      </tr>
                      <tr className="bg-gray-50">
                        <td className="border p-2 font-semibold">Delivered Packages</td>
                        <td className="border p-2 text-center">{formatValue(cortex.parsed?.deliveredPackages)}</td>
                        <td className="border p-2 text-center">{formatValue(wst.parsed?.deliveredPackages)}</td>
                        <td className="border p-2 text-center cursor-pointer hover:bg-blue-100"
                          onClick={() => {
                            if (cortex.parsed?.deliveredPackages && wst.parsed?.deliveredPackages && 
                                cortex.parsed.deliveredPackages !== wst.parsed.deliveredPackages) {
                              openVarianceModal('deliveredPackages', Math.abs(cortex.parsed.deliveredPackages - wst.parsed.deliveredPackages));
                            }
                          }}
                        >
                          {cortex.parsed?.deliveredPackages && wst.parsed?.deliveredPackages
                            ? Math.abs(cortex.parsed.deliveredPackages - wst.parsed.deliveredPackages)
                            : 'N/A'}
                        </td>
                      </tr>
                      <tr>
                        <td className="border p-2 font-semibold">DSP Late Cancel (Max)</td>
                        <td className="border p-2 text-center">{formatValue(cortex.parsed?.dspLateCancelMax)}</td>
                        <td className="border p-2 text-center">{formatValue(wst.parsed?.dspLateCancelMax)}</td>
                        <td className="border p-2 text-center">-</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Line Items Comparison */}
              {((cortex.parsed?.lineItems && cortex.parsed.lineItems.length > 0) || (wst.parsed?.lineItems && wst.parsed.lineItems.length > 0)) && (
                <div>
                  <h3 className="text-sm font-semibold text-gray-700 mb-2">Line Items Summary</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {cortex.parsed?.lineItems && cortex.parsed.lineItems.length > 0 && (
                      <div className="border rounded p-4 bg-gray-50">
                        <h4 className="font-semibold text-gray-800 mb-2">Cortex Line Items ({cortex.parsed.lineItems.length})</h4>
                        <div className="overflow-y-auto max-h-64 text-xs">
                          <table className="w-full border-collapse">
                            <thead>
                              <tr className="bg-gray-300">
                                <th className="border p-1 text-left">Label</th>
                                <th className="border p-1 text-center">Packages</th>
                                <th className="border p-1 text-center">Routes</th>
                              </tr>
                            </thead>
                            <tbody>
                              {cortex.parsed.lineItems.map((item, idx) => (
                                <tr key={idx} className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-100'}>
                                  <td className="border p-1">{item.label}</td>
                                  <td className="border p-1 text-center">{formatValue(item.deliveredPackages)}</td>
                                  <td className="border p-1 text-center">{formatValue(item.completedRoutes)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                    {wst.parsed?.lineItems && wst.parsed.lineItems.length > 0 && (
                      <div className="border rounded p-4 bg-gray-50">
                        <h4 className="font-semibold text-gray-800 mb-2">WST Line Items ({wst.parsed.lineItems.length})</h4>
                        <div className="overflow-y-auto max-h-64 text-xs">
                          <table className="w-full border-collapse">
                            <thead>
                              <tr className="bg-gray-300">
                                <th className="border p-1 text-left">Label</th>
                                <th className="border p-1 text-center">Packages</th>
                                <th className="border p-1 text-center">Routes</th>
                              </tr>
                            </thead>
                            <tbody>
                              {wst.parsed.lineItems.map((item, idx) => (
                                <tr key={idx} className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-100'}>
                                  <td className="border p-1">{item.label}</td>
                                  <td className="border p-1 text-center">{formatValue(item.deliveredPackages)}</td>
                                  <td className="border p-1 text-center">{formatValue(item.completedRoutes)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* --- Variance Response Modal --- */}
          {varianceModal.isOpen && (
            <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
              <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
                <h2 className="text-lg font-bold text-gray-900 mb-4">
                  {varianceModal.metric === 'completedRoutes' ? 'Route Count Variance' : 'Package Count Variance'}
                </h2>
                <p className="text-sm text-gray-600 mb-4">
                  Variance detected: <span className="font-semibold">{varianceModal.variance}</span>
                </p>

                {/* Justification Input */}
                <div className="mb-4">
                  <label className="block text-sm font-semibold text-gray-700 mb-2">
                    Justification *
                  </label>
                  <textarea
                    className="w-full border border-gray-300 rounded p-2 text-sm font-sans"
                    rows={4}
                    placeholder="Explain the reason for this variance..."
                    value={currentJustification}
                    onChange={e => setCurrentJustification(e.target.value)}
                  />
                </div>

                {/* Dispute Status Selection */}
                <div className="mb-6">
                  <label className="block text-sm font-semibold text-gray-700 mb-2">
                    Status *
                  </label>
                  <div className="space-y-2">
                    <label className="flex items-center space-x-2">
                      <input
                        type="radio"
                        name="status"
                        value="acknowledged"
                        checked={currentDisputeStatus === 'acknowledged'}
                        onChange={() => setCurrentDisputeStatus('acknowledged')}
                        className="w-4 h-4"
                      />
                      <span className="text-sm text-gray-700">Acknowledged (variance is expected/explained)</span>
                    </label>
                    <label className="flex items-center space-x-2">
                      <input
                        type="radio"
                        name="status"
                        value="dispute_submitted"
                        checked={currentDisputeStatus === 'dispute_submitted'}
                        onChange={() => setCurrentDisputeStatus('dispute_submitted')}
                        className="w-4 h-4"
                      />
                      <span className="text-sm text-gray-700">Dispute Submitted (needs escalation)</span>
                    </label>
                  </div>
                </div>

                {/* Buttons */}
                <div className="flex gap-3">
                  <button
                    className="flex-1 px-4 py-2 bg-gray-500 text-white rounded-md font-semibold hover:bg-gray-600"
                    onClick={() => {
                      setVarianceModal({ isOpen: false, metric: null, variance: 0 });
                      setCurrentJustification('');
                      setCurrentDisputeStatus(null);
                    }}
                    type="button"
                  >
                    Cancel
                  </button>
                  <button
                    className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-md font-semibold hover:bg-blue-700"
                    onClick={saveVarianceResponse}
                    type="button"
                  >
                    Save
                  </button>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </ProtectedRoute>
  );
}
