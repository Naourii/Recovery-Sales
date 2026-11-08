import { apiGet } from "./api";
import { LeadListSchema, type LeadSummary } from "./schemas";

const PAGE_SIZE = 200; // the backend caps a page at 200

/** Fetches every lead, page by page. */
export async function fetchAllLeads(signal: AbortSignal): Promise<LeadSummary[]> {
  const items: LeadSummary[] = [];
  for (let offset = 0; ; offset += PAGE_SIZE) {
    const page = await apiGet(`/leads?limit=${PAGE_SIZE}&offset=${offset}`, LeadListSchema, { signal });
    items.push(...page.items);
    if (page.items.length < PAGE_SIZE || items.length >= page.total) break;
  }
  return items;
}