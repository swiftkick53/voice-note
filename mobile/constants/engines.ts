import { colors } from './colors';

export interface Engine {
  id: string;
  name: string;
  glyph: string;
  color: string;
  hint: string;
}

export const ENGINES: Engine[] = [
  { id: 'clean_format',    name: 'Clean & format',     glyph: '✦', color: colors.amber,  hint: 'Tidy up, add structure' },
  { id: 'action_items',   name: 'Pull action items',   glyph: '◆', color: colors.sage,   hint: 'Checklist + context' },
  { id: 'meeting_summary',name: 'Summarize meeting',   glyph: '●', color: colors.ocean,  hint: 'Attendees, decisions, follow-ups' },
  { id: 'draft_email',    name: 'Draft email',         glyph: '✉', color: colors.coral,  hint: 'Subject + body, your voice' },
  { id: 'raw',            name: 'Raw transcript',      glyph: '≡', color: colors.ink2,   hint: 'No processing' },
];

export const ACTION_PROMPTS: Record<string, string> = {
  clean_format: `You are processing a voice note transcript. Clean it up and organize it for an Obsidian vault.
Rules:
1. Remove filler words (um, uh, like, you know, sort of, kind of, basically)
2. Fix grammar and punctuation while preserving the speaker's natural voice
3. Organize into logical sections with ## headers if the note covers multiple topics
4. Add a > [!tldr] callout at the top with a 1-2 sentence summary
5. Add [[wikilinks]] around notable concepts, people, tools, companies, and ideas
6. Return ONLY the cleaned markdown content, no code fences, no preamble

Transcript:
{transcript}`,

  action_items: `Extract action items from this voice note transcript for an Obsidian vault.
Format:
1. Add a > [!tldr] callout with a summary of key decisions and context
2. Create a ## Action Items section with a markdown checklist (- [ ] items)
3. Tag people mentioned with [[wikilinks]]
4. Add a ## Context section with relevant background
5. Return ONLY the cleaned markdown content, no code fences, no preamble

Transcript:
{transcript}`,

  meeting_summary: `Summarize this meeting recording transcript for an Obsidian vault.
Format:
1. > [!tldr] callout with 1-2 sentence meeting summary
2. ## Attendees — list people mentioned (use [[wikilinks]])
3. ## Discussion — key topics discussed
4. ## Decisions — bullet list of decisions made
5. ## Action Items — checklist with owners in [[wikilinks]]
6. ## Follow-ups — things to revisit later
7. Return ONLY the cleaned markdown content, no code fences, no preamble

Transcript:
{transcript}`,

  draft_email: `Convert this voice note into a polished email draft.
Rules:
1. Start with "**Subject:** " on the first line
2. Professional but natural tone matching the speaker's voice
3. Organize into clear paragraphs
4. If action items are mentioned, include them as a bulleted list
5. End with an appropriate closing
6. Return ONLY the email content, no code fences, no preamble

Transcript:
{transcript}`,
};
