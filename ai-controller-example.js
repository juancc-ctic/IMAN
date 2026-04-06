const { to, resError, resSuccess } = require("../../services/utils");
const CONFIG = require("../../config/config");
const {
  AI_CONFIG,
  CTIC_CONTACT,
  FREQUENT_CONTACTS_EXAMPLE,
  CTIC_EMPLOYEES_EXAMPLE
} = require("../../config/ai-config");
const logger = require("../../utils/winston");
const axios = require("axios");
const multer = require("multer");
const ContactErpController = require("../registro/contactErp-controller");
const { simpleParser } = require("mailparser");
const fs = require("fs");
const path = require("path");
const os = require("os");
const { exec } = require("child_process");
const { promisify } = require("util");
const { sanitizeFilename } = require("../../utils/fileUtils");

const execAsync = promisify(exec);

// Configure multer for file uploads
const storage = multer.memoryStorage();
const upload = multer({
  storage: storage,
  limits: {
    fileSize: 50 * 1024 * 1024, // 50MB limit
  },
});


// Allowed mime-types for document processing
// Only these file types will be processed with Docling
const ALLOWED_MIME_TYPES = [
  "application/pdf",                                                                      // PDF files
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",            // DOCX files
  "image/jpeg",                                                                           // JPEG Images
  "image/png",                                                                            // PNG Images
  "image/webp",                                                                           // WebP Images
  "application/xml",                                                                      // XML files
  "text/xml"                                                                              // XML files (alternative MIME)
];

/**
 * Check if a MIME type is a "safe" document type for classification fallback.
 * When classification fails (conversion or LLM error), we accept these types by default.
 * @param {string} mimeType - The MIME type to check
 * @returns {boolean} - True if it's a safe document type (PDF, Office docs)
 */
const isSafeDocumentType = (mimeType) =>
  mimeType === 'application/pdf' ||
  mimeType.includes('officedocument') ||
  mimeType === 'application/msword' ||
  mimeType === 'application/xml' ||
  mimeType === 'text/xml';

/**
 * Check if a MIME type is an XML file
 * @param {string} mimeType - The MIME type to check
 * @returns {boolean} - True if it's an XML file
 */
const isXMLFile = (mimeType) => {
  const normalizedType = mimeType.toLowerCase().split(';')[0].trim();
  return normalizedType === 'application/xml' || normalizedType === 'text/xml';
};

/**
 * Check if a MIME type is an Excel file (xls/xlsx). These are allowed for upload but not processed by AI.
 * @param {string} mimeType - The MIME type to check
 * @returns {boolean} - True if it's an Excel file
 */
const isExcelFile = (mimeType) => {
  const normalizedType = mimeType.toLowerCase().split(';')[0].trim();
  return normalizedType === 'application/vnd.ms-excel' ||
    normalizedType === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
};

/**
 * XML element names that contain binary/cryptographic data to be filtered out.
 * These are commonly found in signed XML documents like Facturae invoices.
 */
const XML_BINARY_ELEMENTS = [
  // Digital signature elements (ds: namespace)
  'ds:signature', 'signature',
  'ds:signaturevalue', 'signaturevalue',
  'ds:digestvalue', 'digestvalue',
  'ds:x509certificate', 'x509certificate',
  'ds:x509data', 'x509data',
  'ds:keyinfo', 'keyinfo',
  'ds:keyvalue', 'keyvalue',
  'ds:rsakeyvalue', 'rsakeyvalue',
  'ds:modulus', 'modulus',
  'ds:exponent', 'exponent',
  // ETSI signature elements
  'etsi:signedproperties', 'signedproperties',
  'etsi:qualifyingproperties', 'qualifyingproperties',
  'etsi:signingcertificate', 'signingcertificate',
  'etsi:certdigest', 'certdigest',
];

/**
 * Clean XML buffer by removing binary/cryptographic fields and passing verbatim.
 * This removes signature data, certificates, and other binary content while
 * preserving the semantic structure of the XML document.
 *
 * @param {Buffer} xmlBuffer - The XML file buffer
 * @param {string} filename - Original filename for logging
 * @returns {Promise<{success: boolean, text: string, error?: string}>}
 */
const extractTextFromXML = async (xmlBuffer, filename) => {
  try {
    logger.info("[XML_PROCESSING] Cleaning XML file (removing binary fields)", {
      filename,
      bufferSize: xmlBuffer.length
    });

    const xmlString = xmlBuffer.toString('utf-8');

    // Start with the original XML
    let cleanedXml = xmlString;

    // Remove binary element content (signature, certificates, etc.)
    for (const elementName of XML_BINARY_ELEMENTS) {
      // Match both self-closing and content tags, case-insensitive
      // Handles namespace prefixes like ds:Signature or just Signature
      const regex = new RegExp(
        `<${elementName}[^>]*(?:/>|>[\\s\\S]*?</${elementName}>)`,
        'gi'
      );
      cleanedXml = cleanedXml.replace(regex, '');
    }

    // Add filename context as a prefix
    const finalText = `Original filename: ${filename}\n\n${cleanedXml}`;

    logger.info("[XML_PROCESSING] Successfully cleaned XML", {
      filename,
      originalSize: xmlBuffer.length,
      cleanedSize: finalText.length
    });

    return {
      success: true,
      text: finalText
    };
  } catch (error) {
    logger.error("[XML_PROCESSING] Error cleaning XML:", {
      error: error.message,
      filename,
      stack: error.stack
    });
    return {
      success: false,
      text: '',
      error: error.message
    };
  }
};

/**
 * Get CTIC employees list for exclusion from contact extraction
 * @returns {Array} Array of CTIC employees
 */
const getCticEmployees = () => {
  return CTIC_EMPLOYEES_EXAMPLE;
};

/**
 * Get frequent contacts limited by FREQUENT_CONTACTS_LIMIT with clamping
 * @returns {Array} Array of frequent contacts (clamped to actual array size)
 */
const getFrequentContacts = () => {
  const limit = AI_CONFIG.FREQUENT_CONTACTS_LIMIT;
  const maxContacts = FREQUENT_CONTACTS_EXAMPLE.length;
  // Clamp: min 0, max actual array size
  const clampedLimit = Math.max(0, Math.min(limit, maxContacts));
  return FREQUENT_CONTACTS_EXAMPLE.slice(0, clampedLimit);
};

/**
 * Generate the frequent contacts context string for LLM prompts
 * @returns {string} The formatted context string or empty string if disabled
 */
const getFrequentContactsContextString = () => {
  const useFrequentContacts = AI_CONFIG.USE_FREQUENT_CONTACTS === "true" || AI_CONFIG.USE_FREQUENT_CONTACTS === true;

  if (!useFrequentContacts) {
    return "";
  }

  const frequentContacts = getFrequentContacts();
  const frequentContactsJson = JSON.stringify(frequentContacts, null, 2);

  return `
## Known Contacts Database
Below is a JSON array of known contacts from our records system. When extracting entities from documents, use this list to:
1. Resolve ambiguous mentions to the most likely known entity
2. Normalize variant spellings/abbreviations to canonical names
3. Prefer high-frequency contacts when multiple matches are plausible

<known_contacts>
${frequentContactsJson}
</known_contacts>

## Guidelines for Contact Matching
Please prioritize known contacts when there is a clear match, especially exact name matches or well-known aliases. 
However, treat this list as a suggestion to resolve ambiguities, not a strict constraint. 
- **Prefer Exact Matches**: If a name in the document exactly matches a "canonical_name" or "alias" in the list, use the canonical name.
- **Avoid Overfitting**: If the document clearly mentions a different entity not in the list, extract it as is. Do not force a match to a known contact if the evidence is weak.

### Exclusions
**IGNORE all references to our own organization and its employees.** Do not extract any mention of:
- Fundación CTIC
- CTIC Centro Tecnológico
- CTIC
- Any @ctic.es or @fundacionctic.org email addresses
- Any CTIC employees listed below (treat them as CTIC itself):

<ctic_employees>
${JSON.stringify(getCticEmployees(), null, 2)}
</ctic_employees>
`;
};

// ============================================================================
// MULTIMODAL DOCUMENT PROCESSING FUNCTIONS
// ============================================================================

/**
 * Convert PDF pages to base64-encoded PNG images using pdftoppm
 * @param {Buffer} pdfBuffer - The PDF file buffer
 * @param {number} maxPages - Maximum number of pages to convert (default from config)
 * @returns {Promise<{success: boolean, images: string[], error?: string}>}
 */
const convertPdfToImages = async (pdfBuffer, maxPages = AI_CONFIG.MULTIMODAL_PAGES_TO_EXTRACT) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pdf-convert-'));
  const pdfPath = path.join(tempDir, 'input.pdf');
  const outputPrefix = path.join(tempDir, 'page');

  try {
    logger.info("[MULTIMODAL] Converting PDF to images", {
      bufferSize: pdfBuffer.length,
      maxPages
    });

    // Write PDF buffer to temp file
    fs.writeFileSync(pdfPath, pdfBuffer);

    // Convert PDF to PNG images using pdftoppm
    // -png: output PNG format
    // -r 150: 150 DPI resolution (balance between quality and size)
    // -l maxPages: limit to first N pages
    const cmd = `pdftoppm -png -r 150 -l ${maxPages} "${pdfPath}" "${outputPrefix}"`;

    await execAsync(cmd);

    // Read all generated PNG files
    const files = fs.readdirSync(tempDir)
      .filter(f => f.startsWith('page') && f.endsWith('.png'))
      .sort(); // Ensure correct page order

    if (files.length === 0) {
      throw new Error('No images generated from PDF');
    }

    const images = files.map(file => {
      const imagePath = path.join(tempDir, file);
      const imageBuffer = fs.readFileSync(imagePath);
      return imageBuffer.toString('base64');
    });

    logger.info("[MULTIMODAL] PDF converted successfully", {
      pagesConverted: images.length,
      totalSize: images.reduce((sum, img) => sum + img.length, 0)
    });

    return { success: true, images };
  } catch (error) {
    logger.error("[MULTIMODAL] Error converting PDF to images:", {
      error: error.message,
      stack: error.stack
    });
    return { success: false, images: [], error: error.message };
  } finally {
    // Cleanup temp directory
    try {
      const files = fs.readdirSync(tempDir);
      files.forEach(file => fs.unlinkSync(path.join(tempDir, file)));
      fs.rmdirSync(tempDir);
    } catch (cleanupError) {
      logger.warn("[MULTIMODAL] Error cleaning up temp files:", cleanupError.message);
    }
  }
};

/**
 * Convert Office documents (DOCX, PPTX, etc.) to base64-encoded PNG images
 * Uses LibreOffice to convert to PDF first, then pdftoppm for images
 * @param {Buffer} docBuffer - The document file buffer
 * @param {string} filename - Original filename (used to determine format)
 * @param {number} maxPages - Maximum number of pages to convert
 * @returns {Promise<{success: boolean, images: string[], error?: string}>}
 */
const convertOfficeToImages = async (docBuffer, filename, maxPages = AI_CONFIG.MULTIMODAL_PAGES_TO_EXTRACT) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'office-convert-'));
  // Sanitize filename to prevent path traversal and invalid characters
  const safeFilename = sanitizeFilename(filename);
  const docPath = path.join(tempDir, safeFilename);

  try {
    logger.info("[MULTIMODAL] Converting Office document to images", {
      filename,
      safeFilename,
      bufferSize: docBuffer.length,
      maxPages
    });

    // Write document buffer to temp file
    fs.writeFileSync(docPath, docBuffer);

    // Convert to PDF using LibreOffice headless
    // --headless: run without GUI
    // --convert-to pdf: convert to PDF format
    // --outdir: output directory
    const convertCmd = `soffice --headless --convert-to pdf --outdir "${tempDir}" "${docPath}"`;

    await execAsync(convertCmd, { timeout: 333000 });

    // Find the generated PDF file
    const pdfFiles = fs.readdirSync(tempDir).filter(f => f.endsWith('.pdf'));

    if (pdfFiles.length === 0) {
      throw new Error('LibreOffice failed to generate PDF');
    }

    const pdfPath = path.join(tempDir, pdfFiles[0]);
    const pdfBuffer = fs.readFileSync(pdfPath);

    // Now convert PDF to images using the PDF converter
    const result = await convertPdfToImages(pdfBuffer, maxPages);

    logger.info("[MULTIMODAL] Office document converted successfully", {
      filename,
      pagesConverted: result.images.length
    });

    return result;
  } catch (error) {
    logger.error("[MULTIMODAL] Error converting Office document to images:", {
      error: error.message,
      filename,
      stack: error.stack
    });
    return { success: false, images: [], error: error.message };
  } finally {
    // Cleanup temp directory
    try {
      const files = fs.readdirSync(tempDir);
      files.forEach(file => fs.unlinkSync(path.join(tempDir, file)));
      fs.rmdirSync(tempDir);
    } catch (cleanupError) {
      logger.warn("[MULTIMODAL] Error cleaning up temp files:", cleanupError.message);
    }
  }
};

/**
 * Convert image buffer to base64-encoded string
 * @param {Buffer} imageBuffer - The image file buffer
 * @param {string} mimeType - The MIME type of the image
 * @returns {Promise<{success: boolean, images: string[], mimeType: string, error?: string}>}
 */
const convertImageToBase64 = async (imageBuffer, mimeType) => {
  try {
    logger.info("[MULTIMODAL] Encoding image to base64", {
      bufferSize: imageBuffer.length,
      mimeType
    });

    const base64Image = imageBuffer.toString('base64');

    logger.info("[MULTIMODAL] Image encoded successfully", {
      base64Length: base64Image.length
    });

    return {
      success: true,
      images: [base64Image],
      mimeType: mimeType // Preserve original MIME type for proper data URI
    };
  } catch (error) {
    logger.error("[MULTIMODAL] Error encoding image to base64:", {
      error: error.message,
      mimeType
    });
    return { success: false, images: [], mimeType, error: error.message };
  }
};

/**
 * MIME types supported for multimodal processing
 */
const MULTIMODAL_SUPPORTED_TYPES = {
  // PDF files
  pdf: ['application/pdf'],
  // Office documents (converted via LibreOffice)
  office: [
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document', // DOCX
    'application/vnd.openxmlformats-officedocument.presentationml.presentation', // PPTX
    // XLS/XLSX intentionally excluded: allowed for upload but not processed by AI
    'application/msword', // DOC
    'application/vnd.ms-powerpoint', // PPT
    'application/vnd.oasis.opendocument.text', // ODT
    'application/vnd.oasis.opendocument.presentation', // ODP
    'application/vnd.oasis.opendocument.spreadsheet', // ODS
  ],
  // Image files (direct encoding)
  image: [
    'image/jpeg',
    'image/jpg',
    'image/png',
    'image/webp',
    'image/gif',
  ],
};

/**
 * Check if a MIME type is supported for multimodal processing
 * @param {string} mimeType - The MIME type to check
 * @returns {string|null} - The category ('pdf', 'office', 'image') or null if unsupported
 */
const getMultimodalCategory = (mimeType) => {
  const normalizedType = mimeType.toLowerCase().split(';')[0].trim();

  for (const [category, types] of Object.entries(MULTIMODAL_SUPPORTED_TYPES)) {
    if (types.includes(normalizedType)) {
      return category;
    }
  }
  return null;
};

/**
 * Convert any supported file to base64-encoded images for multimodal processing
 * Routes to appropriate converter based on MIME type
 * @param {Buffer} buffer - The file buffer
 * @param {string} filename - Original filename
 * @param {string} mimeType - The MIME type of the file
 * @param {number} maxPages - Maximum pages to convert (for multi-page documents)
 * @returns {Promise<{success: boolean, images: string[], mimeType?: string, error?: string}>}
 */
const convertFileToImages = async (buffer, filename, mimeType, maxPages = AI_CONFIG.MULTIMODAL_PAGES_TO_EXTRACT) => {
  const category = getMultimodalCategory(mimeType);

  logger.info("[MULTIMODAL] Converting file to images", {
    filename,
    mimeType,
    category,
    bufferSize: buffer.length,
    maxPages
  });

  if (!category) {
    logger.warn("[MULTIMODAL] Unsupported MIME type for multimodal processing", {
      filename,
      mimeType
    });
    return {
      success: false,
      images: [],
      error: `Unsupported MIME type: ${mimeType}`
    };
  }

  switch (category) {
    case 'pdf':
      return await convertPdfToImages(buffer, maxPages);

    case 'office':
      return await convertOfficeToImages(buffer, filename, maxPages);

    case 'image':
      return await convertImageToBase64(buffer, mimeType);

    default:
      return {
        success: false,
        images: [],
        error: `Unknown category: ${category}`
      };
  }
};

/**
 * Save debug images to disk for inspection
 * @param {string[]} images - Array of base64-encoded images
 * @param {string} originalFilename - Original filename for naming
 * @param {string} mimeType - MIME type of images
 * @returns {string|null} - Path to debug directory or null if disabled/failed
 */
const saveDebugImages = (images, originalFilename, mimeType = 'image/png') => {
  if (!AI_CONFIG.MULTIMODAL_DEBUG_SAVE_IMAGES) {
    return null;
  }

  try {
    // Create timestamp-based subdirectory
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const safeName = (originalFilename || 'unknown').replace(/[^a-zA-Z0-9.-]/g, '_');
    const debugDir = path.join(AI_CONFIG.MULTIMODAL_DEBUG_DIR, `${timestamp}_${safeName}`);

    // Ensure directory exists
    fs.mkdirSync(debugDir, { recursive: true });

    // Determine file extension based on mime type
    const extMap = {
      'image/png': '.png',
      'image/jpeg': '.jpg',
      'image/jpg': '.jpg',
      'image/webp': '.webp',
      'image/gif': '.gif',
    };
    const ext = extMap[mimeType] || '.png';

    // Save each image
    images.forEach((base64Image, index) => {
      const imagePath = path.join(debugDir, `page_${String(index + 1).padStart(3, '0')}${ext}`);
      const imageBuffer = Buffer.from(base64Image, 'base64');
      fs.writeFileSync(imagePath, imageBuffer);
    });

    logger.info("[MULTIMODAL_DEBUG] Saved debug images", {
      directory: debugDir,
      imageCount: images.length,
      originalFilename
    });

    return debugDir;
  } catch (error) {
    logger.warn("[MULTIMODAL_DEBUG] Failed to save debug images:", {
      error: error.message,
      originalFilename
    });
    return null;
  }
};

/**
 * Process document images with multimodal LLM to extract structured information
 * @param {string[]} images - Array of base64-encoded images
 * @param {string} imageMimeType - MIME type of images (default: 'image/png')
 * @param {boolean} isEMLContent - Whether this is EML email content
 * @param {string} emailDirection - Email direction: 'incoming' or 'outgoing' (optional)
 * @param {number} contactLimit - Number of external contacts to extract (default: 1)
 * @param {string} originalFilename - Original filename for context
 * @returns {Promise<Object>} - Extracted document information
 */
const processDocumentWithMultimodalLLM = async (
  images,
  imageMimeType = 'image/png',
  isEMLContent = false,
  emailDirection = null,
  contactLimit = 1,
  originalFilename = ''
) => {
  try {
    // Save debug images if enabled
    const debugDir = saveDebugImages(images, originalFilename, imageMimeType);
    if (debugDir) {
      logger.info("[MULTIMODAL_LLM] Debug images saved to:", debugDir);
    }

    const categoriesText = Object.entries(DOCUMENT_CATEGORIES)
      .map(([cat, desc]) => `- ${cat}: ${desc}`)
      .join("\n");

    // Determine if we have a known direction
    const isIncoming = emailDirection === 'incoming';
    const isOutgoing = emailDirection === 'outgoing';
    // Build the text prompt - direction is always known
    const externalRole = isIncoming ? 'sender' : 'receiver';
    const cticRole = isIncoming ? 'receiver' : 'sender';
    const directionDescription = isIncoming
      ? 'INCOMING (received by CTIC from an external party)'
      : 'OUTGOING (sent by CTIC to an external party)';

    const frequentContactsContext = getFrequentContactsContextString();

    let promptText;

    if (isEMLContent) {
      promptText = `Analyze the following document image(s) from an email and extract structured information.
${originalFilename ? `Original filename: ${originalFilename}` : ''}

DOCUMENT DIRECTION: This is an ${directionDescription} email/document.
- Fundación CTIC is the ${cticRole.toUpperCase()} (we already know this - DO NOT include CTIC in your response)
- Your task: Identify up to ${contactLimit} EXTERNAL ${externalRole.toUpperCase()}(S) (the other party/parties that are NOT CTIC)

${frequentContactsContext}

Return a JSON object with these fields:
- reasoning_process: Brief explanation of your reasoning (fill this FIRST)
- title: A concise title for the email/document in Spanish
- summary: A detailed summary (4-6 sentences) in Spanish covering main purpose, key information, and context
- category: The best matching category from the provided list
- external_contacts: Array of up to ${contactLimit} external parties (${externalRole}) with these fields, listed in order of relevance (MOST important external party FIRST):
  - name: Company or person name (IGNORE suffixes like S.L., S.A., S.L.U., S.A.U.)
  - email: Email address if found (EXCLUDE any @fundacionctic.org or @ctic.es addresses)

CRITICAL RULES FOR IDENTIFYING THE EXTERNAL ${externalRole.toUpperCase()}(S):
1. EXCLUDE CTIC: Do NOT return "Fundación CTIC", "CTIC", or any contact with:
   - Email ending in @fundacionctic.org or @ctic.es

2. WHAT TO LOOK FOR in the document images:
   - Company/organization names in document headers, footers, signatures, letterheads, LOGOS
   - Email addresses of external parties
   - Invoice/quote/contract parties
   - Multiple distinct external parties mentioned in the document (up to ${contactLimit})

3. ORDER: List the most relevant/important external party FIRST in the array.

Available categories:
${categoriesText}

Respond only with the JSON object.`;
    } else {
      // Regular document (not EML)
      promptText = `Analyze the following document image(s) and extract structured information.
${originalFilename ? `Original filename: ${originalFilename}` : ''}

DOCUMENT DIRECTION: This is an ${directionDescription} document.
- Fundación CTIC is the ${cticRole.toUpperCase()} (we already know this - DO NOT include CTIC in your response)
- Your task: Identify up to ${contactLimit} EXTERNAL ${externalRole.toUpperCase()}(S) (the other party/parties that are NOT CTIC)

${frequentContactsContext}

Return a JSON object with these fields:
- reasoning_process: Brief explanation of your reasoning (fill this FIRST)
- title: A concise title in Spanish
- summary: A detailed summary (4-6 sentences) in Spanish
- category: The best matching category
- external_contacts: Array of up to ${contactLimit} external parties (${externalRole}) listed in order of relevance (MOST important FIRST) with:
  - name: Company or person name (IGNORE suffixes S.L., S.A., etc.)
  - email: Email address if found (EXCLUDE @fundacionctic.org, @ctic.es)

CRITICAL: Do NOT return CTIC as the external contact. Look for:
- Letterheads, logos, company names at top of document
- Signatures showing the other party
- Invoice/contract parties

Available categories:
${categoriesText}

Respond only with the JSON object.`;
    }

    // Build multimodal message content array
    const messageContent = [
      {
        type: "text",
        text: promptText
      }
    ];

    // Add all images to the message
    for (let i = 0; i < images.length; i++) {
      const mimeType = imageMimeType.startsWith('image/') ? imageMimeType : 'image/png';
      messageContent.push({
        type: "image_url",
        image_url: {
          url: `data:${mimeType};base64,${images[i]}`
        }
      });
    }

    logger.info("[MULTIMODAL_LLM] Making multimodal request to LLM API:", {
      url: `${AI_CONFIG.LLM_BASE_URL}/chat/completions`,
      model: AI_CONFIG.LLM_MODEL,
      imageCount: images.length,
      emailDirection,
      contactLimit,
      originalFilename
    });

    const headers = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${AI_CONFIG.LLM_API_KEY}`,
    };

    // Build system prompt
    const systemPrompt = "You are a helpful assistant that analyzes document images and extracts structured information. Always respond with valid JSON. Ensure your response is complete and properly formatted.";

    // Get the JSON schema for structured outputs
    const jsonSchema = AIController._getDocumentAnalysisSchema(isEMLContent, emailDirection, contactLimit);

    const payload = {
      model: AI_CONFIG.LLM_MODEL,
      messages: [
        {
          role: "system",
          content: systemPrompt
        },
        {
          role: "user",
          content: messageContent
        }
      ],
      response_format: {
        type: "json_schema",
        json_schema: {
          name: "document_analysis",
          schema: jsonSchema,
          strict: true
        }
      }
    };

    const response = await axios.post(
      `${AI_CONFIG.LLM_BASE_URL}/chat/completions`,
      payload,
      { headers, timeout: 333000 }
    );

    logger.info("[MULTIMODAL_LLM] LLM API response received:", {
      status: response.status,
      response_length: response.data?.choices?.[0]?.message?.content?.length || 0
    });

    if (response.status === 200) {
      const llmResponse = response.data?.choices?.[0]?.message?.content;

      if (!llmResponse) {
        logger.error("[MULTIMODAL_LLM] ERROR: llmResponse is empty or undefined!");
        throw new Error("LLM response content is empty");
      }

      try {
        // Try to parse JSON directly, or extract from markdown code block
        let result;
        const jsonMatch = llmResponse.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
        if (jsonMatch) {
          result = JSON.parse(jsonMatch[1]);
        } else {
          // Try direct JSON parse
          result = JSON.parse(llmResponse.trim());
        }

        logger.info("[MULTIMODAL_LLM] LLM response parsed successfully:", {
          hasTitle: !!result.title,
          hasSummary: !!result.summary,
          hasCategory: !!result.category,
          hasExternalContacts: !!result.external_contacts
        });

        // Log the complete LLM response (raw JSON string and parsed object)
        logger.info("[LLM_RESPONSE] ========================================");
        logger.info(`[LLM_RESPONSE] LLM Response - Use Frequent Contacts: ${AI_CONFIG.USE_FREQUENT_CONTACTS}`);
        logger.info(`[LLM_RESPONSE] Raw JSON Response (string): ${llmResponse}`);
        logger.info(`[LLM_RESPONSE] Parsed JSON Response (object): ${JSON.stringify(result, null, 2)}`);
        logger.info("[LLM_RESPONSE] ========================================");

        return {
          success: true,
          data: result
        };
      } catch (parseError) {
        logger.error("[MULTIMODAL_LLM] Failed to parse LLM response as JSON:", {
          error: parseError.message,
          response: llmResponse.substring(0, 500)
        });
        return {
          success: false,
          error: "Invalid JSON response from LLM",
          rawResponse: llmResponse
        };
      }
    } else {
      logger.error(`[MULTIMODAL_LLM] LLM API error: ${response.status}`);
      return {
        success: false,
        error: `LLM API error: ${response.status}`
      };
    }
  } catch (error) {
    logger.error("[MULTIMODAL_LLM] Error calling multimodal LLM:", {
      error: error.message,
      status: error.response?.status,
      responseData: error.response?.data
    });
    return {
      success: false,
      error: error.message
    };
  }
};

// Document categories with descriptions
const DOCUMENT_CATEGORIES = {
  "Acreditacion Certificado o Documento Oficial de Terceros":
    "Official certificates, accreditations, or third-party documents that validate qualifications, compliance, or official status",
  Comunicacion:
    "Internal or external communications, announcements, newsletters, press releases, or general correspondence",
  "Contrato con Cliente":
    "Contracts, agreements, or legal documents establishing formal relationships with clients or customers",
  "Convenio o Acuerdo":
    "Partnership agreements, memorandums of understanding, collaboration agreements, or formal arrangements between parties",
  "Entregable CTIC":
    "Deliverables, reports, or outputs specifically produced by CTIC as part of projects or services",
  Eventos:
    "Event documentation, conference materials, workshop reports, or documentation related to organized activities",
  "Memoria Economica o Justificativa":
    "Financial reports, economic justifications, budget documents, or cost-benefit analyses",
  "Propuesta Economica":
    "Economic proposals, pricing documents, financial offers, or cost proposals for projects or services",
  "Seleccion de Ofertas":
    "Tender evaluation documents, offer selection reports, procurement documentation, or vendor selection materials",
};

const AIController = {
  /**
   * Parse EML file and extract important information
   * @param {Buffer} emlBuffer - The EML file buffer
   * @returns {Object} - Extracted email information
   */
  _parseEMLFile: async (emlBuffer) => {
    try {
      logger.info("[EML_PARSING] Starting EML file parsing", {
        bufferSize: emlBuffer.length
      });

      const parsed = await simpleParser(emlBuffer);

      // Helper function to check if content type is allowed for processing
      const isAllowedMimeType = (contentType) => {
        if (!contentType) return false;
        // Normalize the content type (remove parameters like charset)
        const normalizedType = contentType.toLowerCase().split(';')[0].trim();
        return ALLOWED_MIME_TYPES.includes(normalizedType);
      };

      // Collect all processable attachments
      const processableAttachments = [];

      if (parsed.attachments && parsed.attachments.length > 0) {
        logger.info("[EML_PARSING] Processing attachments", {
          totalAttachments: parsed.attachments.length
        });

        for (let i = 0; i < parsed.attachments.length; i++) {
          const attachment = parsed.attachments[i];
          const filename = attachment.filename || "unknown";
          const contentType = attachment.contentType || "unknown";
          const size = attachment.size || 0;

          logger.info(`[EML_PARSING] Checking attachment ${i + 1}/${parsed.attachments.length}:`, {
            filename,
            contentType,
            size
          });

          // Check if this attachment has an allowed mime-type
          const isAllowed = isAllowedMimeType(contentType);

          // Minimal sanity filter for images (ignore tiny icons < 2KB)
          const isSanityChecked = !contentType.startsWith('image/') || size > 2048;

          if (isAllowed && isSanityChecked) {
            logger.info(`[EML_PARSING] Found processable attachment:`, {
              filename,
              contentType,
              size
            });

            // Ensure content is a Buffer
            let buffer = null;
            if (attachment.content) {
              if (Buffer.isBuffer(attachment.content)) {
                buffer = attachment.content;
              } else if (typeof attachment.content === 'string') {
                buffer = Buffer.from(attachment.content, 'utf-8');
              }
            }

            if (buffer) {
              processableAttachments.push({
                filename,
                contentType,
                size,
                buffer
              });
            } else {
              logger.warn(`[EML_PARSING] No content available for attachment:`, filename);
            }
          } else {
            logger.info(`[EML_PARSING] Skipping attachment:`, {
              filename,
              contentType,
              reason: !isAllowed ? 'unsupported mime-type' : 'failed sanity check'
            });
          }
        }
      }

      // Extract important information
      const extractedInfo = {
        from: parsed.from ? {
          name: parsed.from.text || parsed.from.value?.[0]?.name || "",
          email: parsed.from.value?.[0]?.address || "",
          value: parsed.from.value || []
        } : null,
        to: parsed.to ? {
          name: parsed.to.text || parsed.to.value?.[0]?.name || "",
          email: parsed.to.value?.[0]?.address || "",
          value: parsed.to.value || []
        } : null,
        cc: parsed.cc ? {
          text: parsed.cc.text || "",
          value: parsed.cc.value || []
        } : null,
        bcc: parsed.bcc ? {
          text: parsed.bcc.text || "",
          value: parsed.bcc.value || []
        } : null,
        subject: parsed.subject || "",
        date: parsed.date || null,
        textContent: parsed.text || "",
        htmlContent: parsed.html || "",
        processableAttachments: processableAttachments,
        totalAttachments: parsed.attachments ? parsed.attachments.length : 0
      };

      logger.info("[EML_PARSING] EML parsing completed successfully", {
        hasFrom: !!extractedInfo.from,
        hasTo: !!extractedInfo.to,
        hasSubject: !!extractedInfo.subject,
        hasTextContent: !!extractedInfo.textContent,
        hasHtmlContent: !!extractedInfo.htmlContent,
        processableAttachments: extractedInfo.processableAttachments.length,
        totalAttachments: extractedInfo.totalAttachments
      });

      return extractedInfo;
    } catch (error) {
      logger.error("[EML_PARSING] Error parsing EML file:", {
        error: error.message,
        stack: error.stack
      });
      throw new Error(`Failed to parse EML file: ${error.message}`);
    }
  },



  /**
   * Classify if an attachment is a relevant document or an irrelevant file (Stage 1 - Universal)
   * This now applies to ALL file types (PDF, DOCX, Images) to filter out brochures/logos.
   * @param {Buffer} buffer - The file buffer
   * @param {string} filename - Original filename
   * @param {string} mimeType - MIME type of the file
   * @returns {Promise<Object>} - Classification result { summary: string, isDocument: boolean, type: string, confidence: number }
   */
  _classifyAttachment: async (buffer, filename, mimeType) => {
    try {
      logger.info(`[CLASSIFICATION] Starting Universal Classification for: ${filename} (${mimeType})`, {
        size: buffer.length
      });

      // Convert first 1-2 pages to images for classification
      // This works for PDF, DOCX, and Images
      const conversionResult = await convertFileToImages(buffer, filename, mimeType, 2);

      if (!conversionResult.success || conversionResult.images.length === 0) {
        logger.warn(`[CLASSIFICATION] Failed to convert attachment to images for classification: ${filename}`, {
          error: conversionResult.error
        });
        // Fallback: If conversion fails, we default to ACCEPTING standard docs (safety) and REJECTING images
        // This avoids blocking valid PDFs if our conversion tool is flaky
        return { summary: filename, isDocument: isSafeDocumentType(mimeType), type: 'unknown_unconverted', confidence: 0 };
      }

      const imageMimeType = conversionResult.mimeType || 'image/png';
      const images = conversionResult.images;

      // Build multimodal message for classification
      // UPDATED PROMPT: Explicitly ask to identify brochures, catalogs, and marketing junk, and generate a summary in Spanish
      const promptText = `Analyze this document/image from an email attachment. 
Classify if it is a **relevant business document** (like an invoice, contract, purchase order, formal letter, technical report, or official document) that requires processing, 
or if it is an **irrelevant file** (like a logo, icon, email signature, marketing brochure, product catalog, newsletter, event flyer, or decorative image).
Also provide a brief summary IN SPANISH (30-40 words) describing the document's content and purpose.`;

      const messageContent = [
        {
          type: "text",
          text: promptText
        }
      ];

      // Add up to 2 pages/images for classification
      for (let i = 0; i < Math.min(images.length, 2); i++) {
        messageContent.push({
          type: "image_url",
          image_url: {
            url: `data:${imageMimeType};base64,${images[i]}`
          }
        });
      }

      const headers = {
        "Content-Type": "application/json",
        Authorization: `Bearer ${AI_CONFIG.LLM_API_KEY}`,
      };

      const payload = {
        model: AI_CONFIG.LLM_MODEL,
        messages: [
          {
            role: "system",
            content: "You are a professional document classifier. Your goal is to distinguish between specific relevant business documents (invoices, contracts) and irrelevant noise (logos, brochures, flyers). Generate a brief descriptive summary IN SPANISH for each document. Always respond with structured JSON."
          },
          {
            role: "user",
            content: messageContent
          }
        ],
        response_format: {
          type: "json_schema",
          json_schema: {
            name: "classification_result",
            schema: {
              type: "object",
              properties: {
                summary: {
                  type: "string",
                  description: "A brief summary IN SPANISH (30-40 words) describing the document's content and purpose, e.g., 'Factura emitida por Acme Corp por servicios de consultoría tecnológica prestados durante el mes de enero, incluyendo desarrollo de software y soporte técnico' or 'Folleto promocional de productos de equipamiento industrial con especificaciones técnicas y precios'"
                },
                isDocument: {
                  type: "boolean",
                  description: "True if it is a relevant business document (invoice, contract, formal letter). False if it is irrelevant (brochure, catalog, logo, icon, newsletter)."
                },
                type: {
                  type: "string",
                  description: "The specific type identified (e.g., 'invoice', 'contract', 'brochure', 'logo', 'newsletter')"
                },
                confidence: {
                  type: "number",
                  description: "Confidence in the classification (0.0 to 1.0)"
                }
              },
              required: ["summary", "isDocument", "type", "confidence"],
              additionalProperties: false
            },
            strict: true
          }
        }
      };

      logger.info(`[CLASSIFICATION] Calling LLM Stage 1 (Universal) for ${filename}`);

      const response = await axios.post(
        `${AI_CONFIG.LLM_BASE_URL}/chat/completions`,
        payload,
        { headers, timeout: 333000 }
      );

      if (response.status === 200) {
        const content = response.data?.choices?.[0]?.message?.content;
        if (!content) throw new Error("Empty response from classification LLM");

        const result = JSON.parse(content);
        logger.info(`[CLASSIFICATION] Result for ${filename}:`, result);
        return result;
      } else {
        throw new Error(`LLM API returned status ${response.status}`);
      }
    } catch (error) {
      logger.error(`[CLASSIFICATION] Error classifying ${filename}:`, {
        message: error.message,
        stack: error.stack
      });
      // Safety Fallback: If LLM fails, accept PDF/Docs, reject images
      return { summary: filename, isDocument: isSafeDocumentType(mimeType), type: 'error_fallback', confidence: 0 };
    }
  },

  /**
   * Search for contacts in ERP system using name and/or email
   * Returns all matching contacts with their scores, sorted by sum of metrics (combined + tsv + fuzzy)
   */
  _searchContactInERP: async (name, email) => {
    try {
      logger.info(`[ERP_SEARCH] Starting contact search - Name: "${name}", Email: "${email}"`);

      const mockReq = {
        body: {
          name: name || undefined,
          email: email || undefined,
          threshold: 0.3,
          includeScores: true
        },
      };

      logger.debug(`[ERP_SEARCH] Mock request body:`, mockReq.body);

      // Create a mock response object to capture the result
      let searchResult = null;
      const mockRes = {
        json: (data) => {
          searchResult = data;
          logger.debug(`[ERP_SEARCH] Mock response received:`, data);
        },
        status: (code) => ({
          json: (data) => {
            searchResult = data;
            logger.debug(`[ERP_SEARCH] Mock response with status ${code}:`, data);
          },
        }),
      };

      logger.info(`[ERP_SEARCH] Calling ContactErpController.postSearchContactListHybrid with includeScores=true`);

      await ContactErpController.postSearchContactListHybrid(mockReq, mockRes);

      logger.debug(`[ERP_SEARCH] Raw search result:`, searchResult);

      // Check if we got valid results
      if (searchResult && searchResult.ReadMultiple_Result) {
        const contacts = searchResult.ReadMultiple_Result?.ContactosCard;
        logger.info(`[ERP_SEARCH] Found ${contacts ? contacts.length : 0} contacts in ERP response`);

        if (contacts && Array.isArray(contacts) && contacts.length > 0) {
          // Filter contacts: REQUIRE tsv_score > 0 (and optionally fuzzy_score > 0)
          // This filters out bad matches that only have fuzzy score without TSV relevance
          const validContacts = contacts.filter(contact => {
            const tsv = contact.tsv_score || 0;
            const fuzzy = contact.fuzzy_score || 0;
            // REQUIRE: tsv > 0 (mandatory)
            // OPTIONAL: also require fuzzy > 0 for stricter filtering (uncomment next line)
            return tsv > 0; // && fuzzy > 0; // Uncomment for stricter filtering
          });

          if (validContacts.length === 0) {
            logger.warn(`[ERP_SEARCH] NO VALID MATCHES - All ${contacts.length} contacts filtered out (require tsv_score > 0):`, {
              original_name: name,
              original_email: email,
              filtered_contacts: contacts.map(c => ({
                name: c.Name,
                tsv: c.tsv_score || 0,
                fuzzy: c.fuzzy_score || 0,
                combined: c.combined_score || 0
              }))
            });
            return { found: false, contact: null, allContacts: [], totalScore: 0 };
          }

          logger.info(`[ERP_SEARCH] Filtered ${validContacts.length} valid contacts (tsv > 0) from ${contacts.length} total results`);

          // Calculate total score (sum of combined + tsv + fuzzy) for each VALID contact
          const contactsWithTotalScore = validContacts.map((contact, index) => {
            const tsv = contact.tsv_score || 0;
            const fuzzy = contact.fuzzy_score || 0;
            const combined = contact.combined_score || 0;
            const totalScore = combined + tsv + fuzzy;

            return {
              contact: {
                name: contact.Name || name,
                identifier: contact.VAT_Registration_No || '',
                no: contact.No,
              },
              scores: {
                combined: combined,
                tsv: tsv,
                fuzzy: fuzzy,
                total: totalScore
              },
              originalIndex: index
            };
          });

          // Sort by total score (descending) - contact with highest sum of metrics first
          contactsWithTotalScore.sort((a, b) => b.scores.total - a.scores.total);

          const bestMatch = contactsWithTotalScore[0];

          logger.info(`[ERP_SEARCH] SUCCESS - Best contact found (total score: ${bestMatch.scores.total.toFixed(3)}, tsv: ${bestMatch.scores.tsv.toFixed(3)}):`, {
            erp_name: bestMatch.contact.name,
            erp_identifier: bestMatch.contact.identifier,
            erp_no: bestMatch.contact.no,
            scores: bestMatch.scores,
            original_name: name,
            original_email: email
          });

          // Return all VALID contacts with scores, sorted by total score
          return {
            found: true,
            contact: bestMatch.contact,
            allContacts: contactsWithTotalScore.map(item => ({
              contact: item.contact,
              scores: item.scores
            })),
            totalScore: bestMatch.scores.total
          };
        }
      }

      logger.warn(`[ERP_SEARCH] NO MATCH - Contact not found in ERP for name: "${name}", email: "${email}"`);
      return { found: false, contact: null, allContacts: [], totalScore: 0 };
    } catch (error) {
      logger.error(`[ERP_SEARCH] ERROR - Failed to search contact in ERP:`, {
        error: error.message,
        stack: error.stack,
        name: name,
        email: email
      });
      return { found: false, contact: null, allContacts: [], totalScore: 0 };
    }
  },

  /**
   * Process multimodal LLM result and search contacts in ERP
   * This mirrors the contact processing logic from _processDocumentWithLLM
   * @param {Object} llmData - The parsed LLM response data
   * @param {boolean} isEMLContent - Whether this is EML email content
   * @param {string} emailDirection - Email direction: 'incoming' or 'outgoing'
   * @param {number} contactLimit - Number of external contacts to extract
   * @returns {Object} - Processed result with contacts searched in ERP
   */
  _processMultimodalResult: async (llmData, isEMLContent = false, emailDirection = null, contactLimit = 1) => {
    try {
      logger.info("[MULTIMODAL_RESULT] Processing multimodal LLM result");

      const isIncoming = emailDirection === 'incoming';
      const isOutgoing = emailDirection === 'outgoing';
      const updatedContacts = [];
      const unmatchedContacts = [];

      // Helper to check if a contact is actually CTIC
      // NOTE: CTIC's identifier (G33906637) is intentionally NOT mentioned in LLM prompts
      // to prevent the "pink elephant effect" where explicit mention causes hallucination.
      // Instead, we rely on this post-processing filter to catch any CTIC contacts.
      const isCticContact = (contact) => {
        if (!contact) return false;
        const nameLower = (contact.name || '').toLowerCase();
        const emailLower = (contact.email || '').toLowerCase();

        return (
          nameLower.includes('ctic') ||
          nameLower.includes('fundacion centro tecnologico') ||
          nameLower.includes('fundación centro tecnológico') ||
          emailLower.includes('@fundacionctic.org') ||
          emailLower.includes('@ctic.es')
        );
      };

      // Helper to process a single external contact
      const processExternalContact = async (contact, externalRole) => {
        let processedContact = contact;

        if (isCticContact(processedContact)) {
          logger.warn("[MULTIMODAL_RESULT] LLM returned CTIC as external contact");
          processedContact = {
            name: "CONTACTO EXTERNO NO IDENTIFICADO",
            email: null
          };
          unmatchedContacts.push({
            name: contact.name,
            role: externalRole,
            reason: 'llm_returned_ctic_as_external'
          });
          return { contact: processedContact, found: false, totalScore: 0 };
        }

        // Helper to map confidence to score
        const getConfidenceScore = (confidence) => {
          if (!confidence) return 10;
          switch (confidence.toLowerCase()) {
            case 'high': return 100;
            case 'medium': return 50;
            case 'low': return 10;
            default: return 10;
          }
        };

        // Bypass ERP search if configured
        if (AI_CONFIG.BYPASS_CONTACT_SEARCH && !isCticContact(processedContact)) {
          logger.info("[MULTIMODAL_RESULT] BYPASSING ERP SEARCH for external contact:", processedContact);
          const score = getConfidenceScore(processedContact.confidence);

          // Return the contact as "found" but without ERP number, using confidence as score
          return {
            contact: {
              ...processedContact,
              erp_no: null // Explicitly null as it wasn't found in ERP
            },
            found: true,
            totalScore: score
          };
        }

        // Search external contact in ERP
        if (processedContact.name || processedContact.email) {
          logger.info("[MULTIMODAL_RESULT] Searching external contact in ERP:", processedContact);
          const [searchErr, searchResult] = await to(
            AIController._searchContactInERP(processedContact.name, processedContact.email)
          );

          if (searchErr) {
            logger.error("[MULTIMODAL_RESULT] Error searching external contact in ERP:", searchErr);
            unmatchedContacts.push({
              name: processedContact.name,
              role: externalRole,
              reason: 'search_error'
            });
            return { contact: processedContact, found: false, totalScore: 0 };
          } else if (searchResult.found) {
            processedContact = {
              name: searchResult.contact.name,
              identifier: searchResult.contact.identifier,
              email: processedContact.email,
              erp_no: searchResult.contact.no,
            };
            logger.info("[MULTIMODAL_RESULT] External contact matched in ERP:", {
              original: contact,
              erp: searchResult.contact,
              totalScore: searchResult.totalScore
            });
            return { contact: processedContact, found: true, totalScore: searchResult.totalScore || 0 };
          } else {
            unmatchedContacts.push({
              name: processedContact.name,
              role: externalRole,
              reason: 'not_found_in_erp'
            });
            logger.warn("[MULTIMODAL_RESULT] External contact not found in ERP:", processedContact);
            return { contact: processedContact, found: false, totalScore: 0 };
          }
        }
        return { contact: processedContact, found: false, totalScore: 0 };
      };

      // Process external_contacts array (always used now)
      if (llmData.external_contacts && Array.isArray(llmData.external_contacts)) {
        const externalRole = isIncoming ? 'sender' : 'receiver';
        const cticRole = isIncoming ? 'receiver' : 'sender';

        logger.info("[MULTIMODAL_RESULT] Processing external contact(s):", {
          contactCount: llmData.external_contacts.length,
          direction: emailDirection
        });

        const processedExternalContactsWithScores = [];
        for (let i = 0; i < llmData.external_contacts.length; i++) {
          const contact = llmData.external_contacts[i];
          const { contact: processedContact, totalScore } = await processExternalContact(contact, externalRole);
          processedExternalContactsWithScores.push({
            contact: processedContact,
            role: externalRole,
            totalScore: totalScore || 0,
            originalIndex: i
          });
        }

        // Sort by totalScore descending (best match first)
        // If scores are equal, prioritize the one that appeared earlier in the LLM response (lower originalIndex = higher LLM priority)
        processedExternalContactsWithScores.sort((a, b) => {
          const scoreDiff = b.totalScore - a.totalScore;
          if (scoreDiff !== 0) return scoreDiff;
          return a.originalIndex - b.originalIndex;
        });

        const processedExternalContacts = processedExternalContactsWithScores.map(item => ({
          ...item.contact,
          role: item.role
        }));

        // Build final contacts array with CTIC contact
        const cticContactWithRole = {
          name: CTIC_CONTACT.name,
          identifier: CTIC_CONTACT.identifier,
          email: CTIC_CONTACT.email,
          role: cticRole
        };

        if (isIncoming) {
          updatedContacts.push(...processedExternalContacts);
          updatedContacts.push(cticContactWithRole);
        } else {
          updatedContacts.push(cticContactWithRole);
          updatedContacts.push(...processedExternalContacts);
        }
      }

      logger.info(`[MULTIMODAL_RESULT] Contact processing complete. ${updatedContacts.length} contacts, ${unmatchedContacts.length} unmatched.`);

      // Identify best contact (the first non-CTIC contact — best score or highest LLM priority)
      let bestContact = null;
      if (updatedContacts.length > 0) {
        const externalContacts = updatedContacts.filter(c => !isCticContact(c));
        if (externalContacts.length > 0) {
          bestContact = externalContacts[0];
        }
      }

      return {
        title: llmData.title || "",
        summary: llmData.summary || "",
        category: llmData.category || "",
        contact_name: bestContact?.name || null,
        contact_identifier: bestContact?.identifier || null,
        contacts: updatedContacts,
        unmatched_contacts: unmatchedContacts,
      };
    } catch (error) {
      logger.error("[MULTIMODAL_RESULT] Error processing multimodal result:", {
        error: error.message,
        stack: error.stack
      });
      return {
        title: llmData.title || "",
        summary: llmData.summary || "",
        category: llmData.category || "",
        contacts: [],
        unmatched_contacts: [],
      };
    }
  },

  /**
   * Synthesize final record fields from email text + attachment metadata
   * This combines the email body content with metadata extracted from each attachment
   * @param {string} emailText - The email body text content
   * @param {string} emailSubject - The original email subject
   * @param {Object} emailFrom - Email from field { name, email }
   * @param {Object} emailTo - Email to field { name, email }
   * @param {Array} attachmentMetadataList - Array of metadata from each attachment processing
   * @param {string} emailDirection - Email direction: 'incoming' or 'outgoing'
   * @param {number} contactLimit - Number of external contacts to extract
   * @param {Array} classifiedAttachments - Summaries from Stage 1 classification (used when full metadata unavailable)
   * @returns {Promise<Object>} - Synthesized record fields
   */
  _synthesizeEmailRecord: async (emailText, emailSubject, emailFrom, emailTo, attachmentMetadataList, emailDirection, contactLimit = 1, classifiedAttachments = []) => {
    try {
      logger.info("[SYNTHESIS] Starting email record synthesis", {
        hasEmailText: !!emailText,
        emailSubject,
        attachmentCount: attachmentMetadataList.length,
        classifiedSummariesCount: classifiedAttachments.length,
        emailDirection,
        contactLimit
      });

      const categoriesText = Object.entries(DOCUMENT_CATEGORIES)
        .map(([cat, desc]) => `- ${cat}: ${desc}`)
        .join("\n");

      const isIncoming = emailDirection === 'incoming';
      const externalRole = isIncoming ? 'sender' : 'receiver';
      const cticRole = isIncoming ? 'receiver' : 'sender';
      const directionDescription = isIncoming
        ? 'INCOMING (received by CTIC from an external party)'
        : 'OUTGOING (sent by CTIC to an external party)';

      const frequentContactsContext = getFrequentContactsContextString();

      // Build attachment metadata as JSON array (excluding reasoning_process)
      // Use full metadata from Stage 2 if available, otherwise use classification summaries from Stage 1
      let attachmentSummary = '';
      let attachmentSectionTitle = '';
      
      if (attachmentMetadataList.length > 0) {
        // Full metadata from Stage 2 processing
        const cleanedMetadata = attachmentMetadataList.map((att, idx) => {
          // Remove reasoning_process, keep everything else
          const { reasoning_process, ...cleanAtt } = att;
          return {
            attachment_number: idx + 1,
            ...cleanAtt
          };
        });
        attachmentSummary = JSON.stringify(cleanedMetadata, null, 2);
        attachmentSectionTitle = 'ATTACHMENT ANALYSIS RESULTS (PRIMARY SOURCE FOR CONTACTS)';
      } else if (classifiedAttachments.length > 0) {
        // Summaries from Stage 1 classification (when MAX_ATTACHMENTS_PER_EMAIL=0)
        // Only high-confidence attachments are included
        attachmentSummary = classifiedAttachments
          .map((att, i) => `${i + 1}. "${att.summary}" (${att.filename}) - Type: ${att.type}, Confidence: ${(att.confidence * 100).toFixed(0)}%`)
          .join('\n');
        attachmentSectionTitle = 'CLASSIFIED ATTACHMENTS (high-confidence summaries, not fully processed)';
        logger.info("[SYNTHESIS] Using high-confidence classification summaries instead of full metadata", {
          summariesCount: classifiedAttachments.length
        });
      }

      const prompt = `You are synthesizing a record from an email and its attachments. Your task is to create a unified record that accurately represents the overall communication.

## ${attachmentSectionTitle || 'ATTACHMENT INFORMATION'}

${attachmentMetadataList.length > 0 
  ? `The following JSON contains the analysis results for each attachment. Use this as the PRIMARY source for identifying external contacts:

<attachment_metadata>
${attachmentSummary}
</attachment_metadata>` 
  : classifiedAttachments.length > 0 
    ? `The following attachments were identified as relevant documents. Use their summaries to understand the context of this email:

<attachment_summaries>
${attachmentSummary}
</attachment_summaries>

Note: Full attachment content was not processed. Use the email body and attachment summaries to infer context.`
    : 'No attachments were found or classified as relevant.'}

${frequentContactsContext}

## EMAIL CONTEXT (SECONDARY SOURCE)

**Direction**: ${directionDescription}
**Original Subject**: ${emailSubject || 'No subject'}
**From**: ${emailFrom?.name || ''} <${emailFrom?.email || ''}>
**To**: ${emailTo?.name || ''} <${emailTo?.email || ''}>

**Email Body**:
${emailText || '[No email body text available]'}

## YOUR TASK

Based on ALL the information above, create a unified record with:

1. **subject**: Create a concise, descriptive title in Spanish that captures the essence of this communication. DO NOT just copy the email subject verbatim - synthesize a meaningful title based on the actual content of the email and attachments.

2. **summary**: Write a comprehensive summary (4-6 sentences) in Spanish that covers:
   - The main purpose of the email
   - Key information from the email body
   - Important details from the attachments (if any)
   - Overall context of the communication

3. **category**: Select the BEST matching category considering both the email and attachments.

4. **external_contacts**: Identify up to ${contactLimit} EXTERNAL ${externalRole.toUpperCase()}(S) (NOT Fundación CTIC), listed in order of relevance (MOST important FIRST).
   - **PRIORITIZE contacts extracted from ATTACHMENTS** over email headers
   - Email headers often show the forwarder, not the actual external party
   - Attachments (invoices, contracts, letters) contain the real external contacts
   - Look for: company names and email addresses
   - EXCLUDE any CTIC contacts (@fundacionctic.org, @ctic.es)
   - The FIRST contact in the array should be the primary external party

Available categories:
${categoriesText}

Respond only with the JSON object.`;

      // Build schema for synthesis
      const categories = Object.keys(DOCUMENT_CATEGORIES);
      const jsonSchema = {
        type: "object",
        properties: {
          reasoning_process: {
            type: "string",
            description: "Brief explanation of your synthesis reasoning: how you combined email and attachment information, why you chose the category, and how you identified the external contact(s)."
          },
          subject: {
            type: "string",
            description: "A concise, descriptive title in Spanish that captures the essence of the email and its attachments. NOT a verbatim copy of the email subject."
          },
          summary: {
            type: "string",
            description: "A comprehensive summary (4-6 sentences) in Spanish covering the email content and attachment information."
          },
          category: {
            type: "string",
            enum: categories,
            description: "The category that best matches this communication considering all content."
          },
          external_contacts: {
            type: "array",
            description: `Up to ${contactLimit} EXTERNAL ${externalRole}(s) consolidated from email and attachments.`,
            items: {
              type: "object",
              properties: {
                name: {
                  type: "string",
                  description: "The contact's name (for companies, ignore suffixes like S.L., S.A., etc.)"
                },
                email: {
                  type: "string",
                  description: "The contact's email address (NOT @fundacionctic.org or @ctic.es)"
                }
              },
              required: ["name"],
              additionalProperties: false
            },
            minItems: 1,
            maxItems: contactLimit
          }
        },
        required: ["reasoning_process", "subject", "summary", "category", "external_contacts"],
        additionalProperties: false
      };

      const headers = {
        "Content-Type": "application/json",
        Authorization: `Bearer ${AI_CONFIG.LLM_API_KEY}`,
      };

      const payload = {
        model: AI_CONFIG.LLM_MODEL,
        messages: [
          {
            role: "system",
            content: "You are a helpful assistant that synthesizes information from emails and their attachments into unified records. Always respond with valid JSON. Ensure your response is complete and properly formatted."
          },
          {
            role: "user",
            content: prompt
          }
        ],
        response_format: {
          type: "json_schema",
          json_schema: {
            name: "email_synthesis",
            schema: jsonSchema,
            strict: true
          }
        }
      };

      logger.info("[SYNTHESIS] Making synthesis request to LLM API", {
        promptLength: prompt.length,
        attachmentCount: attachmentMetadataList.length
      });

      const response = await axios.post(
        `${AI_CONFIG.LLM_BASE_URL}/chat/completions`,
        payload,
        { headers, timeout: 333000 }
      );

      if (response.status === 200) {
        const llmResponse = response.data?.choices?.[0]?.message?.content;

        if (!llmResponse) {
          logger.error("[SYNTHESIS] LLM response is empty");
          throw new Error("LLM response content is empty");
        }

        const result = JSON.parse(llmResponse);

        logger.info("[SYNTHESIS] Synthesis completed successfully", {
          subject: result.subject,
          category: result.category,
          contactCount: result.external_contacts?.length || 0
        });

        // Process contacts through ERP search using the existing helper
        const processedResult = await AIController._processMultimodalResult(
          {
            title: result.subject, // Map subject to title for consistency
            summary: result.summary,
            category: result.category,
            external_contacts: result.external_contacts
          },
          true, // isEMLContent
          emailDirection,
          contactLimit
        );

        return processedResult;
      } else {
        logger.error(`[SYNTHESIS] LLM API error: ${response.status}`);
        throw new Error(`LLM API error: ${response.status}`);
      }
    } catch (error) {
      logger.error("[SYNTHESIS] Error synthesizing email record:", {
        error: error.message,
        stack: error.stack
      });

      // Return a basic result using available information
      return {
        title: emailSubject || "",
        summary: emailText?.substring(0, 500) || "",
        category: attachmentMetadataList[0]?.category || "",
        contacts: [],
        unmatched_contacts: []
      };
    }
  },

  /**
   * Process a markdown document and extract structured information using LLM
   * Supports multimodal processing (converting documents to images) with fallback to Docling
   * Accepts either JSON content or file upload (.md file)
   */
  processDocument: async (req, res) => {
    try {
      logger.info("[DOCUMENT_PROCESSING] Starting document processing request");

      // Log AI configuration for this request
      logger.info("[DOCUMENT_PROCESSING] AI Configuration:", {
        LLM_BASE_URL: AI_CONFIG.LLM_BASE_URL,
        LLM_MODEL: AI_CONFIG.LLM_MODEL,
        LLM_API_KEY: AI_CONFIG.LLM_API_KEY ? `${AI_CONFIG.LLM_API_KEY.substring(0, 8)}...` : 'not set',
        DOCLING_URL: AI_CONFIG.DOCLING_URL,
        CONTACT_LIMIT: AI_CONFIG.CONTACT_LIMIT,
        USE_MULTIMODAL: AI_CONFIG.USE_MULTIMODAL,
        MULTIMODAL_PAGES_TO_EXTRACT: AI_CONFIG.MULTIMODAL_PAGES_TO_EXTRACT,
        MULTIMODAL_DEBUG_SAVE_IMAGES: AI_CONFIG.MULTIMODAL_DEBUG_SAVE_IMAGES,
        MULTIMODAL_DEBUG_DIR: AI_CONFIG.MULTIMODAL_DEBUG_DIR,
      });

      logger.debug("[DOCUMENT_PROCESSING] Request details:", {
        hasFile: !!req.file,
        hasBodyContent: !!(req.body && req.body.content),
        fileSize: req.file ? req.file.size : 0,
        contentLength: req.body?.content ? req.body.content.length : 0,
        useMultimodal: AI_CONFIG.USE_MULTIMODAL
      });

      let documentContent = null;
      let relevantAttachments = [];
      let multimodalResult = null;
      let useDoclingFallback = false;
      let skipAIForExcel = false;

      // Check if it's a file upload
      if (req.file) {
        const fileExtension = req.file.originalname.toLowerCase().split('.').pop();
        const mimeType = req.file.mimetype;
        const isEMLFile = fileExtension === 'eml';
        const emailDirection = req.body?.emailDirection || null;
        const contactLimit = AI_CONFIG.CONTACT_LIMIT;

        if (isEMLFile) {
          // ========== EML FILE PROCESSING ==========
          let emlData;
          let err = null;

          if (req.emlData) {
            logger.info("[DOCUMENT_PROCESSING] Using pre-parsed EML data (skipping re-parsing)");
            emlData = req.emlData;
          } else {
            logger.info("[DOCUMENT_PROCESSING] Processing EML file");
            [err, emlData] = await to(
              AIController._parseEMLFile(req.file.buffer)
            );
          }

          if (err) {
            logger.error("[DOCUMENT_PROCESSING] Error parsing EML file:", err);
            return resError(res, "Error parsing EML file", 500);
          }

          // Create structured content for LLM processing
          documentContent = `Email Information:
From: ${emlData.from ? `${emlData.from.name} <${emlData.from.email}>` : 'Unknown'}
To: ${emlData.to ? `${emlData.to.name} <${emlData.to.email}>` : 'Unknown'}
Subject: ${emlData.subject || 'No subject'}
Date: ${emlData.date ? emlData.date.toISOString() : 'Unknown'}

Email Content:
${emlData.textContent || emlData.htmlContent || 'No content available'}`;


          // Stage 1: Classify all processable attachments (Universal Logic)
          const candidateAttachments = emlData.processableAttachments || [];
          logger.info(`[DOCUMENT_PROCESSING] Found ${candidateAttachments.length} candidate attachments for Stage 1 classification.`);

          for (const att of candidateAttachments) {
            // UNIVERSAL RULE: Classify EVERYTHING (PDF, DOCX, Images) to filter out brochures/junk
            const classification = await AIController._classifyAttachment(att.buffer, att.filename, att.contentType);

            if (classification.isDocument) {
              relevantAttachments.push({
                ...att,
                classification
              });
              logger.info(`[DOCUMENT_PROCESSING] Attachment approved: ${att.filename} (${classification.type})`);
            } else {
              logger.info(`[DOCUMENT_PROCESSING] Attachment rejected: ${att.filename} (${classification.type})`);
            }
          }

          // Store classification summaries from relevant attachments with HIGH confidence (before applying limit)
          // This allows us to include attachment context in synthesis even when MAX_ATTACHMENTS_PER_EMAIL=0
          // Only include attachments that are: 1) classified as documents (isDocument=true) AND 2) high confidence
          const HIGH_CONFIDENCE_THRESHOLD = 0.7;
          const classifiedAttachments = relevantAttachments
            .filter(att => 
              att.classification?.isDocument === true && 
              (att.classification?.confidence || 0) >= HIGH_CONFIDENCE_THRESHOLD
            )
            .map(att => ({
              filename: att.filename,
              summary: att.classification?.summary || att.filename,
              type: att.classification?.type || 'unknown',
              confidence: att.classification?.confidence || 0
            }));

          logger.info(`[DOCUMENT_PROCESSING] Classified ${classifiedAttachments.length} high-confidence attachments with summaries (threshold: ${HIGH_CONFIDENCE_THRESHOLD})`, {
            total: relevantAttachments.length,
            highConfidence: classifiedAttachments.length,
            summaries: classifiedAttachments.map(a => `${a.summary.substring(0, 50)}... (${a.confidence})`)
          });

          // Update processableAttachments to only include the first N relevant attachments
          // This ensures emailMonitorService uploads the same attachments used for AI extraction
          // and we don't waste LLM processing on attachments that won't be uploaded
          const uploadLimit = AI_CONFIG.MAX_ATTACHMENTS_PER_EMAIL;
          emlData.processableAttachments = relevantAttachments.slice(0, uploadLimit);
          relevantAttachments = emlData.processableAttachments;

          // Stage 2: Process each attachment INDIVIDUALLY and collect metadata
          const attachmentMetadataList = [];

          if (relevantAttachments.length > 0) {
            documentContent += `\n\nAttachments identified as relevant documents: ${relevantAttachments.map(a => a.filename).join(', ')}`;

            if (AI_CONFIG.USE_MULTIMODAL) {
              logger.info("[DOCUMENT_PROCESSING] Processing each attachment individually with multimodal LLM", {
                attachmentCount: relevantAttachments.length
              });

              for (const att of relevantAttachments) {
                logger.info(`[DOCUMENT_PROCESSING] Processing attachment: ${att.filename}`);

                const conversionResult = await convertFileToImages(att.buffer, att.filename, att.contentType);

                if (conversionResult.success && conversionResult.images.length > 0) {
                  const attResult = await processDocumentWithMultimodalLLM(
                    conversionResult.images,
                    conversionResult.mimeType || 'image/png',
                    false, // Not EML content - it's an attachment document
                    emailDirection,
                    contactLimit,
                    att.filename
                  );

                  if (attResult.success && attResult.data) {
                    attachmentMetadataList.push({
                      filename: att.filename,
                      title: attResult.data.title,
                      summary: attResult.data.summary,
                      category: attResult.data.category,
                      external_contacts: attResult.data.external_contacts || []
                    });
                    logger.info(`[DOCUMENT_PROCESSING] Successfully extracted metadata from: ${att.filename}`, {
                      title: attResult.data.title,
                      category: attResult.data.category
                    });
                  } else {
                    logger.warn(`[DOCUMENT_PROCESSING] Multimodal extraction failed for ${att.filename}, trying Docling fallback`);
                    // Fallback to Docling for this specific attachment
                    try {
                      const [docErr, docResult] = await to(AIController._processDocumentWithDocling(att.buffer, att.filename));
                      if (!docErr && docResult?.document?.md_content) {
                        documentContent += `\n\n--- Attachment: ${att.filename} ---\n${docResult.document.md_content}`;
                      }
                    } catch (e) {
                      logger.warn(`[DOCUMENT_PROCESSING] Docling fallback also failed for ${att.filename}:`, e);
                    }
                  }
                } else {
                  logger.warn(`[DOCUMENT_PROCESSING] Failed to convert ${att.filename} to images, trying Docling`);
                  try {
                    const [docErr, docResult] = await to(AIController._processDocumentWithDocling(att.buffer, att.filename));
                    if (!docErr && docResult?.document?.md_content) {
                      documentContent += `\n\n--- Attachment: ${att.filename} ---\n${docResult.document.md_content}`;
                    }
                  } catch (e) {
                    logger.warn(`[DOCUMENT_PROCESSING] Docling processing error for ${att.filename}:`, e);
                  }
                }
              }
            } else {
              // Multimodal disabled - use Docling for all attachments
              logger.info("[DOCUMENT_PROCESSING] Multimodal disabled, using Docling for attachments");
              for (const att of relevantAttachments) {
                try {
                  const [docErr, docResult] = await to(AIController._processDocumentWithDocling(att.buffer, att.filename));
                  if (!docErr && docResult?.document?.md_content) {
                    documentContent += `\n\n--- Attachment: ${att.filename} ---\n${docResult.document.md_content}`;
                  } else {
                    logger.warn(`[DOCUMENT_PROCESSING] Docling failed for ${att.filename}:`, docErr);
                  }
                } catch (e) {
                  logger.warn(`[DOCUMENT_PROCESSING] Docling processing error for ${att.filename}:`, e);
                }
              }
            }
          } else {
            logger.warn("[DOCUMENT_PROCESSING] No relevant attachments found after classification.");
          }

          // Stage 3: Synthesize final record from email text + all attachment metadata
          if (attachmentMetadataList.length > 0 || emlData.textContent) {
            logger.info("[DOCUMENT_PROCESSING] Synthesizing final record from email + attachments", {
              hasEmailText: !!emlData.textContent,
              attachmentMetadataCount: attachmentMetadataList.length
            });

            const synthesizedResult = await AIController._synthesizeEmailRecord(
              emlData.textContent || emlData.htmlContent || '',
              emlData.subject,
              emlData.from,
              emlData.to,
              attachmentMetadataList,
              emailDirection,
              contactLimit,
              classifiedAttachments
            );

            // Store synthesized result as multimodalResult for later processing
            multimodalResult = {
              success: true,
              data: {
                title: synthesizedResult.title,
                summary: synthesizedResult.summary,
                category: synthesizedResult.category,
                external_contacts: synthesizedResult.contacts?.filter(c => c.role !== 'receiver' || !c.name?.includes('CTIC')) || []
              },
              synthesized: true, // Mark as synthesized for logging
              contacts: synthesizedResult.contacts,
              unmatched_contacts: synthesizedResult.unmatched_contacts
            };

            logger.info("[DOCUMENT_PROCESSING] Synthesis completed", {
              title: synthesizedResult.title,
              category: synthesizedResult.category,
              contactCount: synthesizedResult.contacts?.length || 0
            });
          }

          logger.info("[DOCUMENT_PROCESSING] Final document content length:", documentContent.length);

        } else {
          // ========== NON-EML FILE PROCESSING ==========
          // User explicitly uploaded this file - skip classification, process directly
          logger.info(`[DOCUMENT_PROCESSING] Processing user-uploaded file: ${req.file.originalname}`);

          // Track for output (user uploads are trusted, no classification needed)
          relevantAttachments.push({
            filename: req.file.originalname,
            contentType: mimeType,
            classification: { isDocument: true, type: 'user_uploaded', confidence: 1.0 }
          });

          // ========== XML FILE PROCESSING (text-only, no multimodal) ==========
          if (isXMLFile(mimeType)) {
            logger.info("[DOCUMENT_PROCESSING] Processing XML file with text-only extraction", {
              filename: req.file.originalname,
              mimeType
            });

            const xmlResult = await extractTextFromXML(req.file.buffer, req.file.originalname);

            if (xmlResult.success) {
              documentContent = xmlResult.text;
              logger.info("[DOCUMENT_PROCESSING] XML text extraction successful", {
                filename: req.file.originalname,
                textLength: documentContent.length
              });
              // Skip multimodal and Docling - we have text content ready for LLM
            } else {
              logger.error("[DOCUMENT_PROCESSING] XML text extraction failed", {
                filename: req.file.originalname,
                error: xmlResult.error
              });
              return resError(res, "Error extracting text from XML file", 500);
            }
          }
          // Excel (xls/xlsx): allowed for upload but not processed by AI
          else if (isExcelFile(mimeType)) {
            logger.info("[DOCUMENT_PROCESSING] Excel file accepted but not processed by AI", {
              filename: req.file.originalname,
              mimeType
            });
            skipAIForExcel = true;
          }
          // Try multimodal processing first if enabled (for non-XML, non-Excel files)
          else if (AI_CONFIG.USE_MULTIMODAL) {
            const multimodalCategory = getMultimodalCategory(mimeType);

            if (multimodalCategory) {
              logger.info("[DOCUMENT_PROCESSING] Trying multimodal processing for file", {
                filename: req.file.originalname,
                mimeType,
                category: multimodalCategory
              });

              const conversionResult = await convertFileToImages(
                req.file.buffer,
                req.file.originalname,
                mimeType
              );

              if (conversionResult.success && conversionResult.images.length > 0) {
                const imageMimeType = conversionResult.mimeType || 'image/png';

                // Process with multimodal LLM
                multimodalResult = await processDocumentWithMultimodalLLM(
                  conversionResult.images,
                  imageMimeType,
                  false, // isEMLContent
                  emailDirection,
                  contactLimit,
                  req.file.originalname
                );

                if (multimodalResult.success) {
                  logger.info("[DOCUMENT_PROCESSING] Multimodal processing successful for file");
                } else {
                  logger.warn("[DOCUMENT_PROCESSING] Multimodal processing failed, falling back to Docling", {
                    error: multimodalResult.error
                  });
                  useDoclingFallback = true;
                }
              } else {
                logger.warn("[DOCUMENT_PROCESSING] Failed to convert file to images, falling back to Docling", {
                  error: conversionResult.error
                });
                useDoclingFallback = true;
              }
            } else {
              logger.info("[DOCUMENT_PROCESSING] File type not supported for multimodal, using Docling");
              useDoclingFallback = true;
            }
          } else {
            useDoclingFallback = true;
          }

          // Docling fallback for non-EML files (skip for Excel - not processed by AI)
          if (!skipAIForExcel && useDoclingFallback && !multimodalResult?.success) {
            logger.info("[DOCUMENT_PROCESSING] Processing file with Docling (fallback)", {
              filename: req.file.originalname,
              mimeType
            });

            const [err, result] = await to(
              AIController._processDocumentWithDocling(req.file.buffer, req.file.originalname)
            );

            if (err) {
              logger.error("[DOCUMENT_PROCESSING] Error processing document with Docling:", err);
              return resError(res, "Error processing document with Docling", 500);
            }
            // Add filename as prefix to the markdown content
            documentContent = `Nombre de archivo original: ${req.file.originalname}\n\n${result.document.md_content}`;
          }
        }
      }
      // Check if it's JSON content (text-only, no multimodal)
      else if (req.body && req.body.content) {
        documentContent = req.body.content;
        if (!documentContent || typeof documentContent !== "string") {
          return resError(res, "Content must be a non-empty string", 400);
        }
      } else {
        return resError(
          res,
          "Either provide a document upload or JSON content",
          400
        );
      }

      // Determine processing parameters
      const isEMLContent = req.file && req.file.originalname.toLowerCase().endsWith('.eml');
      const emailDirection = req.body?.emailDirection || null;
      const contactLimit = AI_CONFIG.CONTACT_LIMIT;

      let result;

      // Excel files: accepted but not processed by AI - return minimal result
      if (req.file && skipAIForExcel) {
        result = {
          title: req.file.originalname,
          summary: "",
          category: "",
          contact_name: null,
          contact_identifier: null,
          contacts: [],
          unmatched_contacts: [],
          ai_processed: false
        };
      }
      // If multimodal processing succeeded, use that result
      else if (multimodalResult?.success && multimodalResult.data) {
        // Check if result is from synthesis (contacts already processed)
        if (multimodalResult.synthesized) {
          logger.info("[DOCUMENT_PROCESSING] Using synthesized result (contacts already processed)");
          result = {
            title: multimodalResult.data.title,
            summary: multimodalResult.data.summary,
            category: multimodalResult.data.category,
            contacts: multimodalResult.contacts || [],
            unmatched_contacts: multimodalResult.unmatched_contacts || [],
            contact_name: multimodalResult.contacts?.find(c => c.role === (emailDirection === 'incoming' ? 'sender' : 'receiver') && !c.name?.includes('CTIC'))?.name || null,
            contact_identifier: multimodalResult.contacts?.find(c => c.role === (emailDirection === 'incoming' ? 'sender' : 'receiver') && !c.name?.includes('CTIC'))?.identifier || null
          };
        } else {
          logger.info("[DOCUMENT_PROCESSING] Using multimodal LLM result");

          // Process contacts through ERP search (similar to _processDocumentWithLLM)
          result = await AIController._processMultimodalResult(
            multimodalResult.data,
            isEMLContent,
            emailDirection,
            contactLimit
          );
        }
      } else {
        // Use traditional Docling + text LLM approach
        logger.info("[DOCUMENT_PROCESSING] Starting document processing with text LLM");
        logger.info("[DOCUMENT_PROCESSING] Document direction:", {
          isEMLContent,
          emailDirection,
          contactLimit,
          bodyFields: Object.keys(req.body || {})
        });

        const [err, llmResult] = await to(
          AIController._processDocumentWithLLM(documentContent, isEMLContent, emailDirection, contactLimit)
        );

        if (err) {
          logger.error("[DOCUMENT_PROCESSING] Error processing document:", err);
          return resError(res, "Error processing document", 500);
        }

        result = llmResult;
      }

      logger.info("[DOCUMENT_PROCESSING] Document processing completed successfully");


      // Create simplified response
      const simplifiedResult = {
        doc_name: result.title || "",
        summary: result.summary || "",
        category: result.category || "",
        contact_identifier: result.contact_identifier || null,
        contact_name: result.contact_name || null,
        contacts: result.contacts || [],
        unmatched_contacts: result.unmatched_contacts || [],
        ai_processed: result.ai_processed !== false,
        processed_attachments: relevantAttachments.map(a => ({
          filename: a.filename,
          contentType: a.contentType,
          type: a.classification?.type || 'unknown'
        }))
      };
      logger.info("[RESPONSE] Final simplified result:", simplifiedResult);
      logger.info("[RESPONSE] Contacts processed:", result.contacts || []);
      logger.info("[RESPONSE] Unmatched contacts:", result.unmatched_contacts || []);

      return resSuccess(res, simplifiedResult, 200);
    } catch (error) {
      logger.error("Error in processDocument:", error);
      return resError(res, "Internal server error", 500);
    }
  },

  /**
   * Process document with Docling to convert to markdown
   * @param {Buffer} documentBuffer - The document buffer (PDF, DOCX, etc.)
   * @param {string} filename - Original filename for format detection
   * @returns {Object} - Docling conversion result
   */
  _processDocumentWithDocling: async (documentBuffer, filename) => {
    try {
      logger.info("[DOCLING] Starting document conversion with Docling", {
        filename,
        bufferSize: documentBuffer.length
      });

      // Convert buffer to base64
      const base64Content = documentBuffer.toString('base64');

      const payload = {
        options: {
          to_formats: ["md"],
          pdf_backend: "dlparse_v4",
          table_mode: "accurate",
          image_export_mode: "placeholder",
          page_range: [1, 10]
        },
        sources: [{
          base64_string: base64Content,
          filename: filename,
          kind: "file"
        }]
      };

      logger.info("[DOCLING] Sending request to Docling API", {
        url: `${AI_CONFIG.DOCLING_URL}/v1/convert/source`,
        filename,
        base64Length: base64Content.length
      });

      const response = await axios.post(
        `${AI_CONFIG.DOCLING_URL}/v1/convert/source`,
        payload,
        {
          headers: {
            'Content-Type': 'application/json'
          },
          timeout: 333000
        }
      );

      logger.info("[DOCLING] Document conversion completed", {
        status: response.status,
        filename
      });

      return response.data;
    } catch (error) {
      logger.error("Error in processDocumentWithDocling:", {
        error: error.message,
        stack: error.stack,
        filename,
        response: error.response?.data
      });
      throw new Error(`Failed to process document with Docling: ${error.message}`);
    }
  },

  /**
   * Get JSON schema for document analysis
   * @param {boolean} isEMLContent - Whether this is EML email content
   * @param {string} emailDirection - Email direction: 'incoming' or 'outgoing'
   * @param {number} contactLimit - Number of external contacts to extract (default: 1)
   * @returns {Object} - JSON schema for structured output
   *
   * Always uses external_contacts array. Direction is always known (incoming/outgoing).
   */
  _getDocumentAnalysisSchema: (isEMLContent = false, emailDirection = null, contactLimit = 1) => {
    const categories = Object.keys(DOCUMENT_CATEGORIES);
    const externalRole = emailDirection === 'incoming' ? 'sender' : 'receiver';

    // Build contact description - direction is always known
    const contactDescription = isEMLContent
      ? `Up to ${contactLimit} EXTERNAL ${externalRole}(s) (NOT Fundación CTIC). Extract from email thread content and attachments. Focus on company/organization names and email addresses. Ignore @fundacionctic.org and @ctic.es contacts. List in order of relevance: the MOST important external party FIRST.`
      : `Up to ${contactLimit} EXTERNAL ${externalRole}(s) (NOT Fundación CTIC). Extract from document headers, footers, letterheads, and signatures. List in order of relevance: the MOST important external party FIRST.`;

    const baseSchema = {
      type: "object",
      properties: {
        reasoning_process: {
          type: "string",
          description: "Brief explanation of your reasoning: what type of document this is, why you chose the category, and how you identified the external contact(s). Complete this FIRST."
        },
        title: {
          type: "string",
          description: isEMLContent
            ? "A concise title for the email/document in Spanish (can be based on the subject or content)"
            : "A concise title for the document in Spanish"
        },
        summary: {
          type: "string",
          description: isEMLContent
            ? "A detailed and comprehensive summary of the email content (4-6 sentences or a full paragraph) in Spanish, covering the main purpose, key information, important details, and context"
            : "A detailed and comprehensive summary of the document content (4-6 sentences or a full paragraph) in Spanish, covering the main purpose, key information, important details, and context"
        },
        category: {
          type: "string",
          enum: categories,
          description: "The category that best matches this document"
        },
        external_contacts: {
          type: "array",
          description: contactDescription,
          items: {
            type: "object",
            properties: {
              name: {
                type: "string",
                description: "The contact's name (for companies, ignore suffixes like S.L., S.A., S.L.U., S.A.U., etc.)"
              },
              email: {
                type: "string",
                description: "The contact's email address (NOT @fundacionctic.org or @ctic.es)"
              }
            },
            required: ["name"],
            additionalProperties: false
          },
          minItems: 1,
          maxItems: contactLimit
        }
      },
      required: ["reasoning_process", "title", "summary", "category", "external_contacts"],
      additionalProperties: false
    };

    return baseSchema;
  },

  /**
   * Process document with LLM to extract structured information
   * @param {string} documentContent - The document content to process
   * @param {boolean} isEMLContent - Whether this is EML email content
   * @param {string} emailDirection - Email direction: 'incoming' or 'outgoing' (only for EML content)
   * @param {number} contactLimit - Number of external contacts to extract (default: 1)
   */
  _processDocumentWithLLM: async (documentContent, isEMLContent = false, emailDirection = null, contactLimit = 1) => {
    try {
      const categoriesText = Object.entries(DOCUMENT_CATEGORIES)
        .map(([cat, desc]) => `- ${cat}: ${desc}`)
        .join("\n");

      // Direction is always known - build prompt accordingly
      const isIncoming = emailDirection === 'incoming';
      const externalRole = isIncoming ? 'sender' : 'receiver';
      const cticRole = isIncoming ? 'receiver' : 'sender';
      const directionDescription = isIncoming
        ? 'INCOMING (received by CTIC from an external party)'
        : 'OUTGOING (sent by CTIC to an external party)';

      const frequentContactsContext = getFrequentContactsContextString();

      let prompt;

      if (isEMLContent) {
        prompt = `Analyze the following email content and extract structured information.

DOCUMENT DIRECTION: This is an ${directionDescription} email/document.
- Fundación CTIC is the ${cticRole.toUpperCase()} (we already know this - DO NOT include CTIC in your response)
- Your task: Identify up to ${contactLimit} EXTERNAL ${externalRole.toUpperCase()}(S) (the other party/parties that are NOT CTIC)

${frequentContactsContext}

Return a JSON object with these fields:
- reasoning_process: Brief explanation of your reasoning (fill this FIRST)
- title: A concise title for the email/document in Spanish
- summary: A detailed summary (4-6 sentences) in Spanish covering main purpose, key information, and context
- category: The best matching category from the provided list
- external_contacts: Array of up to ${contactLimit} external parties (${externalRole}) with these fields, listed in order of relevance (MOST important external party FIRST):
  - name: Company or person name (IGNORE suffixes like S.L., S.A., S.L.U., S.A.U.)
  - email: Email address if found (EXCLUDE any @fundacionctic.org or @ctic.es addresses)

CRITICAL RULES FOR IDENTIFYING THE EXTERNAL ${externalRole.toUpperCase()}(S):
1. EXCLUDE CTIC: Do NOT return "Fundación CTIC", "CTIC", or any contact with:
   - Email ending in @fundacionctic.org or @ctic.es

2. PRIORITY ORDER for finding the external contact(s):
   a) ATTACHMENTS FIRST: Look for letterheads, logos, company names in the actual document
   b) EMAIL THREAD CONTENT: Find the substantive message (ignore simple forwards)
   c) EMAIL HEADERS: Use only as last resort (they often show internal forwarding)

3. WHAT TO LOOK FOR:
   - Company/organization names in document headers, footers, signatures
   - Email addresses of external parties
   - Invoice/quote/contract parties
   - Multiple distinct external parties mentioned in the document (up to ${contactLimit})

4. COMMON MISTAKES TO AVOID:
   - Returning CTIC or any CTIC email
   - Returning noreply@, admin@, system@ addresses
   - Returning email forwarders instead of the actual external party
   - Returning CC'd parties instead of the main external party
   - Returning duplicate contacts (each contact should be unique)

Available categories:
${categoriesText}

Email content:
${documentContent}

Respond only with the JSON object.`;
      } else {
        // Regular document (not EML)
        prompt = `Analyze the following document and extract structured information.

DOCUMENT DIRECTION: This is an ${directionDescription} document.
- Fundación CTIC is the ${cticRole.toUpperCase()} (we already know this - DO NOT include CTIC in your response)
- Your task: Identify up to ${contactLimit} EXTERNAL ${externalRole.toUpperCase()}(S) (the other party/parties that are NOT CTIC)

${frequentContactsContext}

Return a JSON object with these fields:
- reasoning_process: Brief explanation of your reasoning (fill this FIRST)
- title: A concise title in Spanish
- summary: A detailed summary (4-6 sentences) in Spanish
- category: The best matching category
- external_contacts: Array of up to ${contactLimit} external parties (${externalRole}) listed in order of relevance (MOST important FIRST) with:
  - name: Company or person name (IGNORE suffixes S.L., S.A., etc.)
  - email: Email address if found (EXCLUDE @fundacionctic.org, @ctic.es)

CRITICAL: Do NOT return CTIC as the external contact. Look for:
- Letterheads, logos, company names at top of document
- Signatures showing the other party
- Invoice/contract parties

Available categories:
${categoriesText}

Document content:
${documentContent}

Respond only with the JSON object.`;
      }

      const headers = {
        "Content-Type": "application/json",
        Authorization: `Bearer ${AI_CONFIG.LLM_API_KEY}`,
      };

      // Validate and truncate prompt if too long
      const maxPromptLength = 50000;
      let processedPrompt = prompt;
      if (prompt.length > maxPromptLength) {
        logger.warn("[LLM_CALL] Prompt too long, truncating:", {
          originalLength: prompt.length,
          truncatedLength: maxPromptLength
        });
        processedPrompt = prompt.substring(0, maxPromptLength) + "\n\n[Content truncated due to length]";
      }

      // Get the JSON schema for structured outputs
      const jsonSchema = AIController._getDocumentAnalysisSchema(isEMLContent, emailDirection, contactLimit);

      // Build system prompt
      const systemPrompt = "You are a helpful assistant that analyzes documents and extracts structured information. Always respond with valid JSON. Ensure your response is complete and properly formatted.";

      const payload = {
        model: AI_CONFIG.LLM_MODEL,
        messages: [
          {
            role: "system",
            content: systemPrompt,
          },
          {
            role: "user",
            content: processedPrompt,
          },
        ],
        //        temperature: 0.7,
        //        top_p: 0.95,
        //        top_k: 20,
        //        min_p: 0.0,
        //        presence_penalty: 1.5,
        //        max_tokens: 3000,
        response_format: {
          type: "json_schema",
          json_schema: {
            name: "document_analysis",
            schema: jsonSchema,
            strict: true
          }
        }
      };
      logger.info("[LLM_CALL] Making request to LLM API:", {
        url: `${AI_CONFIG.LLM_BASE_URL}/chat/completions`,
        model: AI_CONFIG.LLM_MODEL,
        promptLength: processedPrompt.length,
        originalPromptLength: prompt.length,
        wasTruncated: prompt.length > maxPromptLength,
        emailDirection: emailDirection,
        contactLimit: contactLimit
      });

      const response = await axios.post(
        `${AI_CONFIG.LLM_BASE_URL}/chat/completions`,
        payload,
        { headers, timeout: 333000 }
      );
      logger.info("[LLM_CALL] LLM API response received:", {
        status: response.status,
        response_length: response.data?.choices?.[0]?.message?.content?.length || 0,
        hasData: !!response.data,
        hasChoices: !!response.data?.choices,
        choicesLength: response.data?.choices?.length || 0
      });

      if (response.status === 200) {
        // Debug: Log full response structure
        logger.info("[LLM_CALL] Full response.data structure:", JSON.stringify({
          hasData: !!response.data,
          hasChoices: !!response.data?.choices,
          choicesLength: response.data?.choices?.length || 0,
          firstChoice: response.data?.choices?.[0] ? {
            hasMessage: !!response.data.choices[0].message,
            hasContent: !!response.data.choices[0].message?.content,
            contentType: typeof response.data.choices[0].message?.content,
            contentLength: response.data.choices[0].message?.content?.length || 0
          } : null
        }, null, 2));

        const llmResponse = response.data?.choices?.[0]?.message?.content;

        if (!llmResponse) {
          logger.error("[LLM_RESPONSE] ERROR: llmResponse is empty or undefined!", {
            responseData: JSON.stringify(response.data, null, 2)
          });
          throw new Error("LLM response content is empty");
        }

        try {
          // Parse the JSON response directly (vLLM structured outputs guarantees valid JSON)
          const result = JSON.parse(llmResponse);

          // Log the complete LLM response (raw JSON string and parsed object)
          logger.info("[LLM_RESPONSE] ========================================");
          logger.info(`[LLM_RESPONSE] LLM Response - Use Frequent Contacts: ${AI_CONFIG.USE_FREQUENT_CONTACTS}`);
          logger.info(`[LLM_RESPONSE] Raw JSON Response (string): ${llmResponse}`);
          logger.info(`[LLM_RESPONSE] Parsed JSON Response (object): ${JSON.stringify(result, null, 2)}`);
          logger.info("[LLM_RESPONSE] ========================================");

          logger.info("[DOCUMENT_PROCESSING] LLM response received:", result);

          // Process external_contacts array (always used now)
          const updatedContacts = [];
          const unmatchedContacts = [];

          // Helper to check if a contact is actually CTIC
          // NOTE: CTIC's identifier (G33906637) is intentionally NOT mentioned in LLM prompts
          // to prevent the "pink elephant effect" where explicit mention causes hallucination.
          // Instead, we rely on this post-processing filter to catch any CTIC contacts.
          const isCticContact = (contact) => {
            if (!contact) return false;
            const nameLower = (contact.name || '').toLowerCase();
            const emailLower = (contact.email || '').toLowerCase();

            return (
              nameLower.includes('ctic') ||
              nameLower.includes('fundacion centro tecnologico') ||
              nameLower.includes('fundación centro tecnológico') ||
              emailLower.includes('@fundacionctic.org') ||
              emailLower.includes('@ctic.es')
            );
          };

          // Helper to process a single external contact
          const processExternalContact = async (contact, externalRole) => {
            // Validate the external contact is NOT CTIC
            let processedContact = contact;
            if (isCticContact(processedContact)) {
              logger.warn("[DOCUMENT_PROCESSING] LLM returned CTIC as external contact, this should not happen!", {
                external_contact: processedContact
              });
              // Set to a placeholder - this will need manual correction
              processedContact = {
                name: "CONTACTO EXTERNO NO IDENTIFICADO",
                email: null
              };
              unmatchedContacts.push({
                name: contact.name,
                role: externalRole,
                reason: 'llm_returned_ctic_as_external'
              });
              return { contact: processedContact, found: false, totalScore: 0 };
            }

            // Helper to map confidence to score (duplicated inside processDocument scope)
            const getConfidenceScore = (confidence) => {
              if (!confidence) return 10;
              switch (confidence.toLowerCase()) {
                case 'high': return 100;
                case 'medium': return 50;
                case 'low': return 10;
                default: return 10;
              }
            };

            // Bypass ERP search if configured
            if (AI_CONFIG.BYPASS_CONTACT_SEARCH) {
              logger.info("[DOCUMENT_PROCESSING] BYPASSING ERP SEARCH for external contact:", processedContact);
              const score = getConfidenceScore(processedContact.confidence);

              const bypassedContact = {
                ...processedContact,
                erp_no: null
              };
              // Return with totalScore based on confidence
              return { contact: bypassedContact, found: true, totalScore: score };
            }

            // Search external contact in ERP
            if (processedContact.name || processedContact.email) {
              logger.info("[DOCUMENT_PROCESSING] Searching external contact in ERP:", processedContact);
              const [searchErr, searchResult] = await to(
                AIController._searchContactInERP(processedContact.name, processedContact.email)
              );

              if (searchErr) {
                logger.error("[DOCUMENT_PROCESSING] Error searching external contact in ERP:", searchErr);
                unmatchedContacts.push({
                  name: processedContact.name,
                  role: externalRole,
                  reason: 'search_error'
                });
                return { contact: processedContact, found: false, totalScore: 0 };
              } else if (searchResult.found) {
                processedContact = {
                  name: searchResult.contact.name,
                  identifier: searchResult.contact.identifier,
                  email: processedContact.email,
                  erp_no: searchResult.contact.no,
                };
                logger.info("[DOCUMENT_PROCESSING] External contact matched in ERP:", {
                  original: contact,
                  erp: searchResult.contact,
                  totalScore: searchResult.totalScore
                });
                return { contact: processedContact, found: true, totalScore: searchResult.totalScore || 0 };
              } else {
                unmatchedContacts.push({
                  name: processedContact.name,
                  role: externalRole,
                  reason: 'not_found_in_erp'
                });
                logger.warn("[DOCUMENT_PROCESSING] External contact not found in ERP:", processedContact);
                return { contact: processedContact, found: false, totalScore: 0 };
              }
            }
            return { contact: processedContact, found: false, totalScore: 0 };
          };

          // Process external_contacts array (always used now)
          if (result.external_contacts && Array.isArray(result.external_contacts)) {
            const externalRole = isIncoming ? 'sender' : 'receiver';
            const cticRole = isIncoming ? 'receiver' : 'sender';

            logger.info("[DOCUMENT_PROCESSING] Processing external contact(s):", {
              contactCount: result.external_contacts.length,
              direction: emailDirection
            });

            // Process each external contact and collect with total scores
            const processedExternalContactsWithScores = [];
            for (let i = 0; i < result.external_contacts.length; i++) {
              const contact = result.external_contacts[i];
              const { contact: processedContact, totalScore } = await processExternalContact(contact, externalRole);
              processedExternalContactsWithScores.push({
                contact: processedContact,
                role: externalRole,
                totalScore: totalScore || 0,
                originalIndex: i
              });
            }

            // Sort by totalScore descending (best match first)
            // Total score = combined_score + tsv_score + fuzzy_score
            // If scores are equal, prioritize the one that appeared earlier in the LLM response (lower originalIndex = higher LLM priority)
            processedExternalContactsWithScores.sort((a, b) => {
              const scoreDiff = b.totalScore - a.totalScore;
              if (scoreDiff !== 0) return scoreDiff;
              return a.originalIndex - b.originalIndex;
            });

            // Extract contacts in sorted order (best match first)
            const processedExternalContacts = processedExternalContactsWithScores.map(item => ({
              ...item.contact,
              role: item.role
            }));

            logger.info("[DOCUMENT_PROCESSING] External contacts sorted by total score (combined+tsv+fuzzy) - DESCENDING (best first):", {
              contacts: processedExternalContactsWithScores.map((item, idx) => ({
                name: item.contact.name,
                totalScore: item.totalScore.toFixed(3),
                position: idx,
                isBest: idx === 0
              }))
            });

            // Build the final contacts array with proper roles
            // CTIC contact with its role
            const cticContactWithRole = {
              name: CTIC_CONTACT.name,
              identifier: CTIC_CONTACT.identifier,
              email: CTIC_CONTACT.email,
              role: cticRole
            };

            // Add contacts in consistent order: sender first, then receiver
            if (isIncoming) {
              // Add all external contacts (senders), then CTIC (receiver)
              updatedContacts.push(...processedExternalContacts);
              updatedContacts.push(cticContactWithRole);
            } else {
              // Add CTIC (sender), then all external contacts (receivers)
              updatedContacts.push(cticContactWithRole);
              updatedContacts.push(...processedExternalContacts);
            }

            logger.info("[DOCUMENT_PROCESSING] Final contacts built:", {
              direction: emailDirection,
              totalContacts: updatedContacts.length,
              externalContacts: processedExternalContacts.length,
              sender: updatedContacts.find(c => c.role === 'sender'),
              receiver: updatedContacts.find(c => c.role === 'receiver')
            });
          }

          logger.info(`[DOCUMENT_PROCESSING] Final contact processing complete. ${updatedContacts.length} contacts, ${unmatchedContacts.length} unmatched.`);

          // Identify best contact (the first non-CTIC contact — best score or highest LLM priority)
          let bestContact = null;
          if (updatedContacts.length > 0) {
            const potentialContacts = updatedContacts.filter(c => !isCticContact(c));
            if (potentialContacts.length > 0) {
              bestContact = potentialContacts[0];
            }
          }

          // Validate and clean the response
          return {
            title: result.title || "",
            summary: result.summary || "",
            category: result.category || "",
            contact_name: bestContact?.name || null,
            contact_identifier: bestContact?.identifier || null,
            contacts: updatedContacts,
            unmatched_contacts: unmatchedContacts,
          };
        } catch (parseError) {
          logger.error("Failed to parse LLM response as JSON:", parseError);
          logger.error("LLM response:", llmResponse);
          return {
            error: "Invalid JSON response from LLM",
            title: "",
            summary: "",
            category: "",
            contacts: [],
          };
        }
      } else {
        logger.error(`LLM API error: ${response.status} - ${response.data}`);
        return {
          error: "LLM API error",
          title: "",
          summary: "",
          category: "",
          contacts: [],
        };
      }
    } catch (error) {
      logger.error("Error calling LLM:", {
        error: error.message,
        status: error.response?.status,
        statusText: error.response?.statusText,
        responseData: error.response?.data,
        config: {
          url: error.config?.url,
          method: error.config?.method,
          headers: error.config?.headers
        }
      });
      return {
        error: "Failed to process document with LLM",
        title: "",
        summary: "",
        category: "",
        contacts: [],
      };
    }
  },



  /**
   * Get available document categories
   */
  getCategories: async (req, res) => {
    try {
      const categories = Object.keys(DOCUMENT_CATEGORIES).map((key) => ({
        name: key,
        description: DOCUMENT_CATEGORIES[key],
      }));

      return resSuccess(res, categories, 200);
    } catch (error) {
      logger.error("Error getting categories:", error);
      return resError(res, "Error getting categories", 500);
    }
  },

  /**
   * Health check for AI services
   */
  healthCheck: async (req, res) => {
    try {
      const health = {
        llm_enabled: AI_CONFIG.LLM_ENABLED,
        embeddings_api: "unknown",
        llm_api: "unknown",
        qdrant: "unknown",
      };

      // Check embeddings API
      try {
        await axios.get(`${AI_CONFIG.EMBEDDINGS_API_BASE}/models`, {
          timeout: 5000,
        });
        health.embeddings_api = "healthy";
      } catch (error) {
        health.embeddings_api = "unhealthy";
      }

      // Check LLM API
      if (AI_CONFIG.LLM_ENABLED) {
        try {
          await axios.get(`${AI_CONFIG.LLM_BASE_URL}/models`, {
            timeout: 5000,
          });
          health.llm_api = "healthy";
        } catch (error) {
          health.llm_api = "unhealthy";
        }
      }

      // Check Qdrant
      // TODO: Qdrant integration will be properly implemented later
      /*
      try {
        await axios.get(`${AI_CONFIG.QDRANT_URL}/collections`, { timeout: 5000 });
        health.qdrant = 'healthy';
      } catch (error) {
        health.qdrant = 'unhealthy';
      }
      */
      health.qdrant = "not_implemented";

      return resSuccess(res, health, 200);
    } catch (error) {
      logger.error("Error in health check:", error);
      return resError(res, "Error checking AI services health", 500);
    }
  },
};

// Export the upload middleware for use in routes
AIController.upload = upload;

module.exports = AIController;
