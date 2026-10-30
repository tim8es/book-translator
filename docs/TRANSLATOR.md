
## Original-XHTML EPUB paragraph markers

When a source unit uses `<!-- bt-block:NNNNNN -->` markers, preserve each marker
verbatim, in its original sequence. Translate the prose following that marker
without moving, merging, splitting, or discarding paragraph boundaries. Preserve
explicit `*italic*` emphasis, and retain external hyperlink labels unchanged
unless the workflow gains a verified link-remapping capability. The final EPUB
retains the original XHTML/CSS and replaces text in corresponding blocks; an
altered marker is rejected by translation acceptance and output validation.
