'use client';

import { useCallback, useState, ReactNode } from 'react';

interface UploadZoneProps {
  onDrop: (files: File[]) => void;
  accept: string;
  multiple?: boolean;
  label: string;
  children?: ReactNode;
}

export default function UploadZone({
  onDrop,
  accept,
  multiple = false,
  label,
  children,
}: UploadZoneProps) {
  const [isDragActive, setIsDragActive] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);

  const handleDrag = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.type === 'dragenter' || e.type === 'dragover') {
        setIsDragActive(true);
      } else if (e.type === 'dragleave') {
        setIsDragActive(false);
      }
    },
    []
  );

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragActive(false);

      const files = Array.from(e.dataTransfer.files);
      if (files.length > 0) {
        setFileName(
          files.length === 1
            ? files[0].name
            : `${files.length} files selected`
        );
        onDrop(files);
      }
    },
    [onDrop]
  );

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const files = Array.from(e.target.files);
      setFileName(
        files.length === 1 ? files[0].name : `${files.length} files selected`
      );
      onDrop(files);
    }
  };

  return (
    <div
      onDragEnter={handleDrag}
      onDragLeave={handleDrag}
      onDragOver={handleDrag}
      onDrop={handleDrop}
      className={`p-6 border-2 border-dashed rounded-lg text-center cursor-pointer transition-colors ${
        isDragActive
          ? 'border-ndl-blue bg-ndl-light'
          : 'border-gray-300 bg-gray-50 hover:bg-ndl-light'
      }`}
    >
      <input
        type="file"
        multiple={multiple}
        accept={accept}
        onChange={handleFileInput}
        className="hidden"
        id={`upload-${label}`}
      />
      <label
        htmlFor={`upload-${label}`}
        className="block cursor-pointer"
      >
        {children ? (
          children
        ) : (
          <>
            <svg
              className="mx-auto h-12 w-12 text-ndl-blue mb-2"
              stroke="currentColor"
              fill="none"
              viewBox="0 0 48 48"
            >
              <path
                d="M28 8H12a4 4 0 00-4 4v20a4 4 0 004 4h24a4 4 0 004-4V20m-8-12v12m0 0l-4-4m4 4l4-4"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <p className="text-lg font-semibold text-ndl-blue mb-1">{label}</p>
            <p className="text-sm text-gray-600 mb-2">
              {fileName ? `Selected: ${fileName}` : 'Drag and drop or click to select'}
            </p>
            <p className="text-xs text-gray-500">{accept}</p>
          </>
        )}
      </label>
    </div>
  );
}
