import { FC, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MdAttachFile, MdClose, MdErrorOutline } from 'react-icons/md';
import './FileUploader.scss';

export interface FileItem {
  id: string;
  name: string;
  size: number;
  status: 'pending' | 'uploading' | 'success' | 'warning' | 'error';
  message?: string;
  url?: string;
  file: File; // Add actual File object
}

export interface UploadProgress {
  isUploading: boolean;
  currentFile: number;
  totalFiles: number;
  currentFileName: string;
}

interface FileUploaderProps {
  files: FileItem[];
  onFilesChange: (files: FileItem[]) => void;
  onFileDelete: (fileId: string) => void;
  maxFileSize?: number; // in bytes
  acceptedTypes?: string;
  multiple?: boolean;
  className?: string;
  uploadProgress?: UploadProgress; // Add upload progress prop
}

const FileUploader: FC<FileUploaderProps> = ({
  files,
  onFilesChange,
  onFileDelete,
  maxFileSize = 30 * 1024 * 1024, // 30MB default
  acceptedTypes = '.pdf,.doc,.docx,.html,.htm',
  multiple = true,
  className = '',
  uploadProgress,
}) => {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const validateSingleFile = (file: File): FileItem => {
    const fileItem: FileItem = {
      id: `file-${Date.now()}-${Math.random()}`,
      name: file.name,
      size: file.size,
      status: 'pending',
      file: file,
    };

    // Validate file size
    if (file.size > maxFileSize) {
      return {
        ...fileItem,
        status: 'error',
        message: t('fileUpload.maxSizeExceeded'),
      };
    }

    // Validate file type
    const fileExtension = '.' + file.name.split('.').pop()?.toLowerCase();
    const allowedTypes = acceptedTypes
      .split(',')
      .map((type) => type.trim().toLowerCase());

    if (!allowedTypes.includes(fileExtension)) {
      return {
        ...fileItem,
        status: 'error',
        message: t('fileUpload.wrongFormat'),
      };
    }

    return fileItem;
  };

  const handleFileSelect = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = event.target.files;
    if (!selectedFiles) return;

    const newFiles: FileItem[] = Array.from(selectedFiles).map((file) =>
      validateSingleFile(file)
    );

    const updatedFiles = [...files, ...newFiles];
    onFilesChange(updatedFiles);

    // Reset input value so same file can be selected again
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);

    const droppedFiles = e.dataTransfer.files;
    if (!droppedFiles) return;

    const newFiles: FileItem[] = Array.from(droppedFiles).map((file) =>
      validateSingleFile(file)
    );

    const updatedFiles = [...files, ...newFiles];
    onFilesChange(updatedFiles);
  };

  const renderOverallProgress = () => {
    if (!uploadProgress?.isUploading) return null;

    return (
      <div className="file-uploader__overall-progress">
        <div className="file-uploader__overall-progress-content">
          <MdAttachFile className="file-uploader__progress-icon" />
          <div className="file-uploader__progress-text">
            {t('fileUpload.uploading')} {uploadProgress.currentFile}{' '}
            {t('fileUpload.of')} {uploadProgress.totalFiles}...
          </div>
        </div>
      </div>
    );
  };

  const renderFileList = () => {
    if (files.length === 0) return null;

    return (
      <div className="file-uploader__attachments-list">
        {files.map((file) => (
          <div key={file.id} className="file-uploader__attachment-item">
            <div
              className={`file-uploader__attachment ${
                file.status === 'error' || file.status === 'warning'
                  ? 'file-uploader__attachment--error'
                  : file.status === 'uploading'
                  ? 'file-uploader__attachment--uploading'
                  : file.status === 'success'
                  ? 'file-uploader__attachment--success'
                  : ''
              }`}
            >
              <div className="file-uploader__attachment-name">{file.name}</div>
              <div className="file-uploader__attachment-size">
                {formatFileSize(file.size)}
              </div>
              {(file.status === 'error' || file.status === 'warning') && (
                <MdErrorOutline className="file-uploader__attachment-error-icon" />
              )}
              <button
                className="file-uploader__attachment-delete"
                onClick={() => onFileDelete(file.id)}
                type="button"
                aria-label={`Delete ${file.name}`}
                disabled={uploadProgress?.isUploading}
              >
                <MdClose />
              </button>
            </div>
            {(file.status === 'error' || file.status === 'warning') && (
              <div className="file-uploader__attachment-error-text">
                {file.message || 'Error occurred'}
              </div>
            )}
          </div>
        ))}
      </div>
    );
  };

  return (
    <div className={`file-uploader ${className}`}>
      <div className="file-uploader__input-container">
        {uploadProgress?.isUploading ? (
          renderOverallProgress()
        ) : (
          <div
            className={`file-uploader__input ${
              isDragOver ? 'file-uploader__input--drag-over' : ''
            }`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={handleFileSelect}
          >
            <MdAttachFile className="file-uploader__attach-icon" />
            <div className="file-uploader__instructions">
              {t('fileUpload.dropFilesOrClick') ||
                'Drop files here, or click to browse'}
            </div>
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple={multiple}
          onChange={handleFileChange}
          style={{ display: 'none' }}
          accept={acceptedTypes}
        />
        <div className="file-uploader__helper-texts">
          <div className="file-uploader__helper-text">
            {t('fileUpload.maxFileSize') || 'Max file size'}{' '}
            {formatFileSize(maxFileSize)}
          </div>
        </div>
      </div>

      {renderFileList()}
    </div>
  );
};

export default FileUploader;
