-- Orders-only uploads (e.g. a Shopify orders export) arrive as CSV
update storage.buckets
   set allowed_mime_types = array['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'text/csv']
 where id = 'fasla-files';
