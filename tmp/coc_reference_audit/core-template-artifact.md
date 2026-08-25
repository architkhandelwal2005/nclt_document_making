# CoC retained-template artifact specification

## Governing rule

The four files below are immutable authorities. Runtime templates must be exact
copies and must be modified only in generated output. Package parts that are not
explicitly changed must be copied byte-for-byte.

## Source manifest

| Runtime key | Authoritative source | SHA-256 | Audited pages |
|---|---|---|---:|
| `notice-first` | `reference documents/Mahakali_Foods_Notice_First_CoC_Meeting_Draft.docx` | `C0E79669CB25A35E44246CFD3E61CD18E217001689C418B248AF58E4306BEDFD` | 13 |
| `notice-subsequent` | `reference documents/Notice of 2nd CoC Meeting (1).docx` | `2132868A999EA5545E5F4C15C149BF47BD4F32CEF1AFE5C41ADB325F57F88685` | 12 |
| `minutes-first` | `reference documents/Minutes of 1st CoC meeting Premier Proteins Ltd (1).docx` | `6C92543FEC36C0F21D8326D90B94C0932BC0BA348127C33B9F369C93699ABB80` | 10 |
| `minutes-subsequent` | `reference documents/Minutes of 2nd CoC meeting Premier Proteins Ltd (1).docx` | `324D000DAA5D38B951BA10ED6141D8274CAAE7FC8E079884F873D7B6FFA81DE3` | 8 |

## Shared preservation controls

- Preserve A4 section size, margins, styles, numbering definitions, themes,
  relationships, headers, footers, fields, bookmarks, content controls, page
  borders, and drawing parts unless a named replacement requires a change.
- Never accept tracked changes in an authoritative source and never normalize
  its XML as a side effect of generation.
- Do not generate discussion text. Discussion is a mandatory manual field for
  minutes agenda items.
- Dynamic agenda numbers come from the ordered agenda records, never from the
  sample document's literal numbering.
- A notice snapshot is frozen when issued. Minutes consume that snapshot so a
  later agenda edit cannot silently change the historical notice-to-minutes chain.
- Blank optional data must not create placeholder strings such as `None`,
  `undefined`, or empty punctuation-only rows.

## Notice: first meeting

- Retain: cover composition, contents page, blue section headings, legal notice,
  service-recipient tables, agenda sections, e-voting instructions, notes to
  agenda, signatures, and `Page | N` footer.
- Dynamic slots: company/case identity, IRP identity, meeting number/date/time,
  mode/venue/link, notice date/place, CoC recipients, suspended-management
  recipients, discussion agenda, voting agenda, voting window, and process email.
- Variable blocks: clone existing recipient table rows and agenda list paragraphs;
  remove sample rows/items after replacement.
- Fidelity gate: no Mahakali data may remain unless the selected case is Mahakali.

## Notice: subsequent meeting

- Retain: Premier cover and contents topology, notice clauses, service tables,
  discussion/voting agenda divisions, e-voting instructions, notes, signatures,
  and page-number footer.
- Dynamic slots and variable blocks are the same categories as first notice,
  with meeting type selected by meeting number greater than one.
- Fidelity gate: no Premier data may remain unless the selected case is Premier.

## Minutes: first meeting

- Retain: A4 section, thin gold page border, blue underlined confidential header,
  blue footer rule with matter name and page number, black double body rules,
  uppercase hierarchy, attendance-table design, agenda typography, resolution
  typography, vote-of-thanks and signature treatment.
- Dynamic slots: case/meeting identity, actual start/end, attendance, quorum,
  each frozen agenda title, manual discussion, decision, resolution text, voting
  results, and signing data.
- Variable blocks: clone the native attendance row, agenda heading/title/body,
  resolution paragraphs, and voting-result row.
- Fidelity gate: agenda numbering is contiguous even though the sample is not.

## Minutes: subsequent meeting

- Retain the same running-header, footer, page-border and body hierarchy as the
  first-minutes source, using this document's own agenda/table exemplars.
- Dynamic slots and variable blocks are the same categories as first minutes.
- Fidelity gate: no sample discussion, decision, resolution, creditor, amount,
  or voting result may survive into generated output.

## Visual acceptance

Every generated DOCX must be rendered to PDF and inspected page-by-page. Reject
an output with clipped text, unexpected blank pages, overlapping content,
orphaned headings, inconsistent running elements, broken tables, sample-data
contamination, or unresolved application placeholders.
